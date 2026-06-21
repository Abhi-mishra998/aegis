"""N20 follow-up (2026-06-21) — mesh-JWT operator escape hatch on
/demo/spawn-workspace.

Agent A15 shipped N20a (reject when XFF first hop is not globally-routable)
and N20b (also reject when the immediate TCP peer is loopback or docker-
bridge). That correctly closes the brutal-review attack "spawn 5 tenants
in 2s via SSM-exec", but it also rejects LEGITIMATE operator workflows:

  * a cron / Lambda triggering a demo spawn from inside the VPC for testing
  * a smoke-test script SSH'd into an EC2

The follow-up: if the caller presents a valid ES256 X-Mesh-Token (kid in
``ACP_MESH_TRUSTED_KEYS``), skip the XFF + ALB-hop checks. Only services
inside the mesh can mint valid mesh tokens; the brutal-review attack
model (RCE in ONE service) doesn't grant access to OTHER services' mesh
private keys. The rate-limit is preserved but keyed by ``mesh:<issuer>``.

These five tests cover:

  1. Loopback request, NO mesh token → 403 (N20 reject still works)
  2. Loopback request WITH valid mesh token → bypasses N20 → reaches
     identity-svc → 200
  3. Loopback request with EXPIRED mesh token → 403 (still rejected)
  4. Loopback request with mesh token whose kid is NOT trusted → 403
  5. Public-XFF request from ALB IP → 200 (existing path unchanged)

The handler depends on ``request.app.state.client``, ``get_db``, Redis,
and Turnstile. We stub all four so the test runs in-process without
docker compose.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Make the project importable so ``services.gateway`` resolves regardless of
# whether pytest is invoked with PYTHONPATH set. Same idiom as
# tests/test_n16_n20_ssrf_hardening.py.
#
# Extra care: pytest's default ``prepend`` import-mode walks up looking for
# an ``__init__.py`` and adds the FIRST package-rootless directory to
# sys.path. Because ``tests/services/__init__.py`` exists in this repo,
# pytest implants ``tests/`` into sys.path before ``test_demo_spawn_mesh_
# operator.py`` runs — that shadows the top-level ``services/`` namespace
# package with the empty ``tests/services``. We move the repo root to the
# front of sys.path and evict any partial ``services``/``services.gateway``
# binding so ``from services.gateway.routers import demo`` resolves to the
# real router. Running with ``pytest --import-mode=importlib`` makes this
# unnecessary; the bootstrap below is a belt-and-suspenders for the
# default invocation.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = Path(__file__).resolve().parent
# Belt-and-suspenders against pytest's default ``prepend`` import-mode
# putting ``tests/`` on sys.path BEFORE the repo root — that makes
# ``import services`` resolve to the empty ``tests/services`` package and
# breaks every downstream ``services.gateway`` import. Drop the tests dir
# (it's never needed for runtime imports — tests reference modules by
# their fully-qualified ``tests.<x>`` names if at all) and prepend the
# repo root so the real ``services/`` namespace wins. Then evict any
# stale partial binding so the next ``import services`` re-resolves.
sys.path[:] = [p for p in sys.path if Path(p).resolve() != _TESTS_DIR]
sys.path.insert(0, str(_REPO_ROOT))
# Evict any stale ``services`` binding (e.g. previous test files that
# triggered pytest to import ``tests.services`` as a package — that pollutes
# ``sys.modules['services']`` to point at the empty stub).
for _stale in [k for k in list(sys.modules) if k == "services" or k.startswith("services.")]:
    del sys.modules[_stale]
# Force a fresh resolution of ``services`` from the real namespace package
# so any subsequent ``from services.<x> import y`` inside test bodies hits
# the right path. The import below also catches the failure at module-load
# time instead of inside individual tests, giving a single clear stack.
import importlib  # noqa: E402
_services_mod = importlib.import_module("services")
if str(_REPO_ROOT / "services") not in [str(Path(p).resolve()) for p in _services_mod.__path__]:
    # Defensive — re-anchor the namespace package to the real services/ dir.
    _services_mod.__path__ = [str(_REPO_ROOT / "services")]
    # Drop everything so the next imports re-resolve through the corrected __path__.
    for _stale in [k for k in list(sys.modules) if k == "services" or k.startswith("services.")]:
        del sys.modules[_stale]

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from jose import jwt  # noqa: E402

from sdk.common import auth as mesh_auth  # noqa: E402
from sdk.common.config import settings  # noqa: E402


# ───────────────────────────────────────────────────────────────────────
# Key + token helpers — same pattern as tests/test_mesh_auth.py
# ───────────────────────────────────────────────────────────────────────


def _gen_ec_keypair() -> tuple[str, str]:
    """Return (private_pem_b64, public_pem_b64) for a fresh ES256 keypair."""
    priv = ec.generate_private_key(ec.SECP256R1())
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(priv_pem).decode("ascii"),
        base64.b64encode(pub_pem).decode("ascii"),
    )


def _mint_expired_token(private_pem_b64: str, service_name: str) -> str:
    """Sign a token with exp in the past so the verifier returns _expired=True.
    The minter's normal mint_service_token always sets exp in the future, so
    we hand-roll the JWT here."""
    priv_pem = base64.b64decode(private_pem_b64)
    now = int(time.time())
    payload = {
        "iss":   service_name,
        "aud":   "acp.mesh.internal",
        "iat":   now - 3600,
        "exp":   now - 60,  # expired one minute ago
        "scope": "internal",
    }
    return jwt.encode(
        payload,
        priv_pem.decode("ascii"),
        algorithm="ES256",
        headers={"kid": service_name},
    )


def _mint_untrusted_token(unused_private_pem_b64: str, kid: str) -> str:  # noqa: ARG001
    """Mint with a brand-new keypair under a kid the verifier doesn't trust.
    The argument is kept so the call site reads parallel to _mint_expired_token."""
    rogue_priv_b64, _rogue_pub_b64 = _gen_ec_keypair()
    priv_pem = base64.b64decode(rogue_priv_b64)
    now = int(time.time())
    payload = {
        "iss":   kid,
        "aud":   "acp.mesh.internal",
        "iat":   now,
        "exp":   now + 300,
        "scope": "internal",
    }
    return jwt.encode(
        payload,
        priv_pem.decode("ascii"),
        algorithm="ES256",
        headers={"kid": kid},
    )


# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_mesh_caches(monkeypatch):
    """Each test gets fresh mesh-key caches and a clean env."""
    monkeypatch.delenv("ACP_MESH_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ACP_MESH_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("ACP_MESH_TRUSTED_KEYS", raising=False)
    monkeypatch.delenv("TURNSTILE_SECRET_KEY", raising=False)
    mesh_auth._reset_mesh_caches_for_tests()
    # Turnstile must stay in dev-bypass mode for the test to be hermetic;
    # the production setting is empty by default, so just be defensive.
    monkeypatch.setattr(settings, "TURNSTILE_SECRET_KEY", "", raising=False)
    yield
    mesh_auth._reset_mesh_caches_for_tests()


@pytest.fixture
def trusted_keypair(monkeypatch):
    """Wire a (priv, pub) keypair such that mint_service_token('worker') signs
    with it AND _verify_mesh_jwt accepts the resulting token under kid='worker'.
    Returns (priv_b64, pub_b64, service_name)."""
    priv_b64, pub_b64 = _gen_ec_keypair()
    service_name = "worker"
    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", service_name)
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", priv_b64)
    monkeypatch.setenv(
        "ACP_MESH_TRUSTED_KEYS", json.dumps({service_name: pub_b64})
    )
    mesh_auth._reset_mesh_caches_for_tests()
    return priv_b64, pub_b64, service_name


def _stub_request(
    *,
    client_host: str = "127.0.0.1",
    x_forwarded_for: str | None = None,
    redis_count: int = 1,
    identity_response: dict | None = None,
) -> MagicMock:
    """Build a MagicMock that satisfies every attribute the handler reads.

    * request.client.host
    * request.app.state.client (httpx-style AsyncClient with .post)
    * request.json() (Turnstile reads this — returns {} so dev-bypass kicks in)
    * request.headers (used by mesh_headers cascade — irrelevant here)
    * request.cookies / request.state (untouched by spawn handler)
    """
    req = MagicMock(name="Request")
    req.client = MagicMock()
    req.client.host = client_host
    req.headers = {}
    if x_forwarded_for:
        req.headers["X-Forwarded-For"] = x_forwarded_for

    # request.json() is awaited by the Turnstile branch — keep it awaitable
    # even though the dev-bypass path doesn't actually consume the result.
    req.json = AsyncMock(return_value={})

    # request.app.state.client.post → identity-svc /auth/demo/spawn
    identity_response = identity_response or {
        "data": {
            "tenant_id":   str(uuid.uuid4()),
            "owner_email": "demo+test@aegisagent.in",
            "expires_at":  int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = identity_response
    mock_resp.text = json.dumps(identity_response)

    inner_client = MagicMock()
    inner_client.post = AsyncMock(return_value=mock_resp)
    req.app = MagicMock()
    req.app.state = MagicMock()
    req.app.state.client = inner_client

    return req


def _patch_redis(monkeypatch, *, count: int = 1):
    """Replace sdk.common.redis.get_redis_client with an in-memory stub so the
    rate-limit branch doesn't try to connect to Redis."""
    from sdk.common import redis as redis_mod

    class _StubRedis:
        def __init__(self) -> None:
            self.counters: dict[str, int] = {}
            self.expirations: dict[str, int] = {}

        async def incr(self, key: str) -> int:
            self.counters[key] = self.counters.get(key, 0) + count
            return self.counters[key]

        async def expire(self, key: str, ttl: int) -> bool:
            self.expirations[key] = ttl
            return True

    stub = _StubRedis()
    monkeypatch.setattr(
        redis_mod, "get_redis_client", lambda *a, **kw: stub, raising=False
    )
    return stub


def _run(coro):
    """Drive an async coroutine in a fresh event loop. asyncio.run() works
    cleanly because the handler never spawns background tasks."""
    return asyncio.run(coro)


# ───────────────────────────────────────────────────────────────────────
# Tests — five scenarios from the task brief
# ───────────────────────────────────────────────────────────────────────


def test_1_loopback_without_mesh_token_rejects(monkeypatch, trusted_keypair):
    """Loopback request, NO mesh token → 403. The N20 defence (peer is
    loopback, XFF empty) still wins. This is the canonical brutal-review
    attack — SSM-exec curl from the EC2 host or RCE inside any container."""
    _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    req = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
    with pytest.raises(HTTPException) as ei:
        _run(
            demo_router.spawn_demo_workspace(
                request=req,
                db=MagicMock(),
                x_forwarded_for=None,
                x_mesh_token=None,
            )
        )
    assert ei.value.status_code == 403, "N20 must still reject loopback w/o mesh"
    # The new error body is a dict with structured hints — operators see
    # the internal escape hatch instead of an opaque "use the load balancer".
    assert isinstance(ei.value.detail, dict)
    assert ei.value.detail["error"] == "Forbidden"
    assert "X-Mesh-Token" in ei.value.detail["detail"]
    assert "mint_service_token" in ei.value.detail["hints"]["internal"]


def test_2_loopback_with_valid_mesh_token_allows(monkeypatch, trusted_keypair):
    """Loopback request WITH valid mesh token → bypasses N20 → identity-svc
    is reached (mocked to 200) → 200 returned. This is the operator path."""
    _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    _priv, _pub, svc = trusted_keypair
    token = mesh_auth.mint_service_token(svc)
    assert mesh_auth._verify_mesh_jwt(token) is not None, "fixture sanity"

    req = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
    result = _run(
        demo_router.spawn_demo_workspace(
            request=req,
            db=MagicMock(),
            x_forwarded_for=None,
            x_mesh_token=token,
        )
    )
    assert result["success"] is True
    assert "jwt" in result["data"]
    assert "tenant_id" in result["data"]
    # Identity-svc was called (operator path made it through to the real work)
    req.app.state.client.post.assert_awaited_once()


def test_3_loopback_with_expired_mesh_token_rejects(monkeypatch, trusted_keypair):
    """Loopback request with EXPIRED mesh token → 403. _verify_mesh_jwt
    returns {"_expired": True, ...} for expired tokens; the handler must
    treat that as "not a valid operator" and fall through to the N20
    checks, which still reject loopback."""
    _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    priv_b64, _pub, svc = trusted_keypair
    expired = _mint_expired_token(priv_b64, svc)
    claims = mesh_auth._verify_mesh_jwt(expired)
    assert claims is not None and claims.get("_expired") is True, (
        "fixture sanity — token must verify as expired, not as None"
    )

    req = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
    with pytest.raises(HTTPException) as ei:
        _run(
            demo_router.spawn_demo_workspace(
                request=req,
                db=MagicMock(),
                x_forwarded_for=None,
                x_mesh_token=expired,
            )
        )
    assert ei.value.status_code == 403
    # Identity-svc was NOT called (request never made it past N20)
    req.app.state.client.post.assert_not_called()


def test_4_loopback_with_untrusted_kid_rejects(monkeypatch, trusted_keypair):
    """Loopback request with mesh token whose kid is NOT in trusted_keys → 403.
    _verify_mesh_jwt returns None for unknown kid; the handler treats that
    as "no valid operator credential" and falls through to N20, which
    still rejects loopback."""
    _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    _priv, _pub, _svc = trusted_keypair
    # Mint a token signed by a fresh rogue keypair under an unknown kid.
    bad_token = _mint_untrusted_token(_priv, kid="not-a-real-service")
    assert mesh_auth._verify_mesh_jwt(bad_token) is None, (
        "fixture sanity — kid='not-a-real-service' must be rejected"
    )

    req = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
    with pytest.raises(HTTPException) as ei:
        _run(
            demo_router.spawn_demo_workspace(
                request=req,
                db=MagicMock(),
                x_forwarded_for=None,
                x_mesh_token=bad_token,
            )
        )
    assert ei.value.status_code == 403
    req.app.state.client.post.assert_not_called()


def test_5_public_xff_from_alb_peer_allows(monkeypatch, trusted_keypair):
    """Public-XFF request with ALB private peer → 200. This is the legitimate
    marketing path — verify the existing P2-1 + N20 behaviour is preserved
    (no mesh token in play)."""
    _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    # 8.8.8.8 is is_global True; 10.20.3.5 is RFC1918 private (ALB-like).
    req = _stub_request(client_host="10.20.3.5", x_forwarded_for="8.8.8.8")
    result = _run(
        demo_router.spawn_demo_workspace(
            request=req,
            db=MagicMock(),
            x_forwarded_for="8.8.8.8",
            x_mesh_token=None,
        )
    )
    assert result["success"] is True
    assert "jwt" in result["data"]
    req.app.state.client.post.assert_awaited_once()


# ───────────────────────────────────────────────────────────────────────
# Bonus — rate-limit bucketing
# ───────────────────────────────────────────────────────────────────────


def test_mesh_operator_rate_limit_uses_issuer_bucket(monkeypatch, trusted_keypair):
    """Hammer the mesh-operator path 6 times: the 6th call must 429, and the
    Redis key must be bucketed by ``mesh:<issuer>`` not by source IP. Proves
    a rogue operator script can't accidentally burn another caller's quota."""
    stub = _patch_redis(monkeypatch)
    from services.gateway.routers import demo as demo_router

    _priv, _pub, svc = trusted_keypair
    token = mesh_auth.mint_service_token(svc)

    for i in range(5):
        req = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
        _run(
            demo_router.spawn_demo_workspace(
                request=req,
                db=MagicMock(),
                x_forwarded_for=None,
                x_mesh_token=token,
            )
        )

    # 6th call must 429
    req6 = _stub_request(client_host="127.0.0.1", x_forwarded_for=None)
    with pytest.raises(HTTPException) as ei:
        _run(
            demo_router.spawn_demo_workspace(
                request=req6,
                db=MagicMock(),
                x_forwarded_for=None,
                x_mesh_token=token,
            )
        )
    assert ei.value.status_code == 429
    # Bucket key was the issuer, not 127.0.0.1 or anything IP-shaped
    expected_key = f"acp:demo_spawn_rl:mesh:{svc}"
    assert expected_key in stub.counters, (
        f"expected mesh-bucket key {expected_key!r}, got {list(stub.counters)!r}"
    )
    # And the public-IP bucket (acp:demo_spawn_rl:127.0.0.1) was NEVER written
    assert "acp:demo_spawn_rl:127.0.0.1" not in stub.counters
