"""P-Hard-1 sprint — regression tests for the security fixes landed 2026-06-22.

Scope: covers what can be exercised against a live stack on localhost without
external creds. Tests are tagged ``integration`` so they skip when the gateway
is not reachable on :8000 — matches the existing tests/conftest.py pattern.

Mapping to pentest findings:
  P0-1   /scim/v2/Users must return 401 (not 500) on garbage bearer.
  P1-2   /transparency/{key,keys,roots,consistency} must be anonymous.
  P1-4   gateway/main.py SSE handler must use _REAUTH_INTERVAL_SECONDS = 240.
  P2-2   infra/pgbouncer.aws.ini must not contain the stale acp-postgres-prod host.
  P3-1   /tenant bare path must return JSON (gateway 401), not SPA HTML.
  P3-2   /receipts/key + /transparency/key must be wrapped in the standard envelope.
  P2-10  /transparency/* anon path must not require X-Tenant-ID from the client.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path

import pytest
import urllib.request
import urllib.error
import json
import ssl


REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_BASE = "http://localhost:8000"


def _live() -> bool:
    try:
        sock = socket.create_connection(("localhost", 8000), timeout=2)
        sock.close()
        return True
    except OSError:
        return False


def _get(path: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(f"{GATEWAY_BASE}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read(8192).decode(errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(8192).decode(errors="replace")
        except Exception:
            body = ""
        return e.code, dict(e.headers or {}), body


# ──────────────────────────────────────────────────────────────────────────
# P2-2 — pgbouncer source hostname (file-only, no live stack needed)
# ──────────────────────────────────────────────────────────────────────────


def test_p2_2_pgbouncer_source_has_live_hostname() -> None:
    """The source pgbouncer.aws.ini must reference the live RDS hostname.

    Previously the file shipped with the stale ``acp-postgres-prod.*`` host
    that no longer resolves; ``safe_deploy.sh`` was sed-patching it at
    deploy time. P2-2 fixed the source and added a CI lint to prevent
    regression.
    """
    pgb = (REPO_ROOT / "infra" / "pgbouncer.aws.ini").read_text()
    assert "acp-postgres-prod" not in pgb, (
        "Stale pgbouncer hostname `acp-postgres-prod` reintroduced. "
        "Use `aegis-prod-postgres.cz0qqg60keaj.ap-south-1.rds.amazonaws.com`."
    )
    assert "aegis-prod-postgres.cz0qqg60keaj" in pgb, (
        "Expected live RDS hostname missing from pgbouncer.aws.ini"
    )


# ──────────────────────────────────────────────────────────────────────────
# P1-4 — SSE reauth interval matches the documented Clerk template lifetime
# ──────────────────────────────────────────────────────────────────────────


def test_p1_4_sse_reauth_interval_is_240() -> None:
    """``_REAUTH_INTERVAL_SECONDS`` must be 240 s.

    With the Clerk JWT template ``aegis`` lifetime at 300 s, the SSE
    handler must re-validate at 240 s so it sees a still-valid token
    (60 s safety buffer) and the stream stays open across the natural
    token rotation. Previous value was 30 s with a 60 s token — caused
    a reauth-fail-and-reconnect every minute on every active user.
    """
    main_py = (REPO_ROOT / "services" / "gateway" / "main.py").read_text()
    m = re.search(r"_REAUTH_INTERVAL_SECONDS\s*=\s*([0-9.]+)", main_py)
    assert m, "_REAUTH_INTERVAL_SECONDS constant not found in services/gateway/main.py"
    value = float(m.group(1))
    assert value == 240.0, (
        f"Expected _REAUTH_INTERVAL_SECONDS=240.0; found {value}. "
        f"Update value AND the companion Clerk template lifetime comment."
    )


# ──────────────────────────────────────────────────────────────────────────
# Integration tests — require a live gateway on :8000
# ──────────────────────────────────────────────────────────────────────────

pytestmark_integration = pytest.mark.skipif(not _live(), reason="gateway not reachable on :8000")


@pytestmark_integration
def test_p0_1_scim_garbage_bearer_returns_401_not_500() -> None:
    """P0-1 — every garbage SCIM bearer must 401, never 500.

    Was returning 500 with a SQL error in logs because the scim_tokens
    table didn't exist in acp_audit. P-Hard-1 added the migration
    services/audit/alembic/versions/p2_11_scim_audit_2026_06_22.py and
    wrapped the DB query in try/except to return 503 on infra errors.
    """
    for bearer in ("scim_garbage", "scim_aaaa-bbbb-cccc", "scim_", "scim_' OR 1=1 --"):
        status, _, _ = _get("/scim/v2/Users", {"Authorization": f"Bearer {bearer}"})
        assert status == 401, f"SCIM bearer '{bearer}' → {status}, expected 401"


@pytestmark_integration
@pytest.mark.parametrize(
    "path",
    [
        "/transparency/key",
        "/transparency/keys",
        "/transparency/roots",
        # /consistency requires a from/to query, so we only check it does
        # not require auth — accept 200 (with no params, returns empty)
        # OR 4xx-validation, never 401.
        "/transparency/consistency",
    ],
)
def test_p1_2_transparency_endpoints_are_anonymous(path: str) -> None:
    """P1-2 — transparency surface must be reachable without auth.

    The auth gate previously rejected anonymous probes with 401.
    Offline-verifiability is a core design intent of the transparency
    log; making it auth-only contradicted the docs.
    """
    status, _, _ = _get(path)
    assert status != 401, (
        f"{path} returned 401 — auth gate still on. Check "
        f"services/gateway/middleware.py:_SKIP_PATHS contains this exact path."
    )


@pytestmark_integration
def test_p2_10_transparency_anon_does_not_need_tenant_header() -> None:
    """P2-10 — transparency reads must not require X-Tenant-ID.

    Even after P1-2 opened the auth gate, the downstream audit-svc
    was returning 400 ``X-Tenant-ID required``. P2-10 injects a
    zero/system tenant id from the gateway for anon transparency
    paths. Validate by hitting /transparency/key with no headers
    and asserting we get a JSON body, not the 400 error.
    """
    status, _, body = _get("/transparency/key")
    assert status == 200, f"/transparency/key → {status}, expected 200"
    # Must NOT contain the audit-svc complaint
    assert "X-Tenant-ID required" not in body, (
        "Audit-svc still demanding X-Tenant-ID — check internal_headers() "
        "injection logic in services/gateway/_helpers.py"
    )


@pytestmark_integration
@pytest.mark.parametrize("path", ["/receipts/key", "/transparency/key"])
def test_p3_2_key_endpoints_use_standard_envelope(path: str) -> None:
    """P3-2 — /receipts/key + /transparency/key wrapped in {success, data, ...}.

    Was bare ``{"algorithm": "...", "public_key_pem": "..."}`` —
    SDK consumers unwrapping ``.data`` got KeyError. Now wrapped in
    the standard envelope. Both SDKs (Python + JS) read the value
    with ``body.public_key_pem ?? body.data.public_key_pem`` so they
    survive either shape, but new clients should see only the wrap.
    """
    status, headers, body = _get(path)
    assert status == 200, f"{path} → {status}"
    assert "application/json" in headers.get("Content-Type", "").lower()
    data = json.loads(body)
    assert isinstance(data, dict)
    assert "success" in data, f"{path} response missing 'success' envelope key"
    assert "data" in data, f"{path} response missing 'data' envelope key"
    assert data.get("success") is True
    inner = data.get("data")
    assert isinstance(inner, dict)
    assert "public_key_pem" in inner, f"{path} data.public_key_pem missing"
    assert "algorithm" in inner, f"{path} data.algorithm missing"


@pytestmark_integration
def test_p3_1_tenant_bare_returns_json_not_spa() -> None:
    """P3-1 — bare /tenant path returns gateway JSON, not the SPA HTML.

    nginx previously fell through to the SPA fallback for ``/tenant``
    (no trailing slash), returning 200 + index.html. Pentest scanners
    saw "every URL exists". Fix: nginx exact-match block proxying to
    gateway:8000, which then returns its standard 401.
    """
    status, headers, body = _get("/tenant")
    # Direct gateway probe (no nginx in front): expect JSON 401
    ct = headers.get("Content-Type", "").lower()
    assert "application/json" in ct, (
        f"/tenant Content-Type was {ct!r}; expected application/json. "
        f"Body starts: {body[:120]!r}"
    )
    assert "vite.svg" not in body, "SPA index.html leaked through nginx fallback"


# ──────────────────────────────────────────────────────────────────────────
# P2-11 / N1-1 — sanity checks on the alembic chain (file-only)
# ──────────────────────────────────────────────────────────────────────────


def test_p2_11_audit_alembic_chain_has_single_head() -> None:
    """P2-11 — adding the gateway-owned SCIM migration must not break the chain.

    Parse every revision id in services/audit/alembic/versions/ via AST
    and confirm exactly one head (a revision whose id is not referenced
    as down_revision by any other migration).
    """
    import ast

    versions = REPO_ROOT / "services" / "audit" / "alembic" / "versions"
    rev_ids: set[str] = set()
    down_revs: set[str] = set()
    for f in sorted(versions.glob("*.py")):
        tree = ast.parse(f.read_text())
        for node in ast.iter_child_nodes(tree):
            target = None
            value = None
            if isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            if not isinstance(target, ast.Name) or value is None:
                continue
            if target.id == "revision":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    assert value.value not in rev_ids, (
                        f"Revision id collision: {value.value} appears in "
                        f"more than one migration file"
                    )
                    rev_ids.add(value.value)
            elif target.id == "down_revision":
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    down_revs.add(value.value)
                elif isinstance(value, ast.Tuple):
                    for elt in value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            down_revs.add(elt.value)

    heads = rev_ids - down_revs
    assert len(heads) == 1, (
        f"Expected single audit alembic head, found {len(heads)}: {sorted(heads)}"
    )


def test_p2_11_identity_scim_migration_is_noop_superseded() -> None:
    """P2-11 — the identity-targeted SCIM migration must be a no-op.

    The original migration created scim_tokens in acp_identity, but the
    runtime code in services/gateway/_scim_auth.py queries acp_audit.
    P-Hard-1 marked the identity migration SUPERSEDED with no-op
    upgrade/downgrade bodies and added the real migration to the
    audit-svc alembic chain.
    """
    f = (REPO_ROOT / "services" / "identity" / "alembic" / "versions"
         / "l8m9n0o1p2q3_sprint_ei3_scim_tokens.py")
    src = f.read_text()
    # The header must mark it superseded
    assert "SUPERSEDED" in src, "Identity migration missing SUPERSEDED header note"
    # The upgrade body must NOT call op.create_table
    upgrade_block = src.split("def upgrade")[1].split("def downgrade")[0]
    assert "create_table" not in upgrade_block, (
        "Identity scim_tokens migration still calls create_table — "
        "would create a vestigial empty table on fresh acp_identity"
    )
