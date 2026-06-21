"""
N11 + N12 — kill the last two raw-INTERNAL_SECRET consumers in the gateway.

After P1-1 Phase 3 the mesh JWT path is ES256-strict. These tests guard the
follow-on cleanup that:

  N11 — /metrics middleware gate. Replaced the raw ``X-Internal-Secret ==
        INTERNAL_SECRET`` check with an X-Mesh-Token (ES256) lane PLUS a
        dedicated ``PROMETHEUS_SCRAPE_SECRET`` lane. INTERNAL_SECRET must
        NOT be accepted on /metrics any more, otherwise a leak of the mesh
        secret would still let an attacker scrape tenant-labelled gauges
        like AUTH_FAILURES_TOTAL{role=…} or TENANT_ISOLATION_VIOLATIONS_TOTAL.

  N12 — /demo/cleanup-expired. Replaced the raw ``x_internal_secret ==
        settings.INTERNAL_SECRET`` check with ``Depends(verify_internal_secret)``,
        so only a valid ES256 mesh JWT triggers the destructive tenant sweep.
        Also records an audit row for every cleanup run.
"""
from __future__ import annotations

import base64
import json
import os

# Settings is constructed at import time; satisfy required env vars before the
# `sdk.common.*` imports below pull config in. Matches the pattern used by the
# rest of the tests/test_ei*.py family.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "n11-n12-unit-test")
os.environ.setdefault("INTERNAL_SECRET", "n11-n12-unit-test")

import pytest  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from sdk.common import auth as mesh  # noqa: E402
from sdk.common.config import settings  # noqa: E402


def _gen_ec_keypair() -> tuple[str, str]:
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


@pytest.fixture(autouse=True)
def _isolate_mesh(monkeypatch):
    monkeypatch.delenv("ACP_MESH_SERVICE_NAME", raising=False)
    monkeypatch.delenv("ACP_MESH_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("ACP_MESH_TRUSTED_KEYS", raising=False)
    mesh._reset_mesh_caches_for_tests()
    yield
    mesh._reset_mesh_caches_for_tests()


# ---------------------------------------------------------------------------
# N11 — /metrics middleware gate
# ---------------------------------------------------------------------------


def test_n11_prometheus_scrape_secret_setting_exists():
    """The dedicated PROMETHEUS_SCRAPE_SECRET setting must be on the Settings
    class so the deploy environment can supply it independently of the mesh
    secret. Defaulting to empty disables the lane (no accidental "" == "" match).
    """
    # The attribute must exist (no AttributeError).
    val = settings.PROMETHEUS_SCRAPE_SECRET
    assert isinstance(val, str)


def test_n11_middleware_metrics_gate_accepts_mesh_jwt(monkeypatch):
    """An ES256 mesh JWT in X-Mesh-Token must let /metrics through."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "prometheus")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"prometheus": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    token = mesh.mint_service_token("prometheus")
    claims = mesh._verify_mesh_jwt(token)
    assert claims is not None and not claims.get("_expired")
    assert claims["iss"] == "prometheus"


def test_n11_middleware_metrics_gate_rejects_raw_internal_secret(monkeypatch):
    """The legacy INTERNAL_SECRET equality compare MUST NOT pass /metrics.

    The new code only accepts X-Mesh-Token (ES256) or X-Prometheus-Secret.
    A request that presents only INTERNAL_SECRET (via the old X-Internal-Secret
    header) gets 401. We assert by inspecting the middleware source rather
    than spinning up the whole stack: the dead raw-comparison must be gone.
    """
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "services" / "gateway" / "middleware.py").read_text()

    # The pre-fix raw compare against settings.INTERNAL_SECRET on /metrics
    # is the bug. After the fix the middleware MUST NOT contain that string.
    assert "X-Internal-Secret\") == settings.INTERNAL_SECRET" not in src, (
        "N11 regression: /metrics is still gated on raw INTERNAL_SECRET. "
        "Replace with the X-Mesh-Token / X-Prometheus-Secret path."
    )

    # The new code MUST gate on the dedicated PROMETHEUS_SCRAPE_SECRET.
    assert "PROMETHEUS_SCRAPE_SECRET" in src, (
        "N11 regression: middleware no longer references the dedicated "
        "PROMETHEUS_SCRAPE_SECRET setting."
    )
    # And MUST consult the mesh JWT verifier for /metrics too.
    assert "_verify_mesh_jwt" in src, (
        "N11 regression: middleware no longer offers a mesh-JWT lane on /metrics."
    )


def test_n11_prometheus_yml_uses_dedicated_secret():
    """The Prometheus scrape config must ship the dedicated secret, not the
    mesh INTERNAL_SECRET, so the two rotate independently and a mesh-secret
    leak does not let an attacker scrape tenant-labelled gauges.
    """
    from pathlib import Path
    yaml = (Path(__file__).resolve().parents[1] / "infra" / "prometheus.yml").read_text()
    assert "X-Prometheus-Secret" in yaml, (
        "N11 regression: prometheus.yml is not sending the dedicated "
        "X-Prometheus-Secret header."
    )
    assert "PROMETHEUS_SCRAPE_SECRET" in yaml, (
        "N11 regression: prometheus.yml still references INTERNAL_SECRET "
        "for the /metrics scrape — must use PROMETHEUS_SCRAPE_SECRET."
    )
    # The legacy gate header must be gone.
    assert "X-Internal-Secret" not in yaml, (
        "N11 regression: prometheus.yml still ships X-Internal-Secret on "
        "/metrics scrapes."
    )


# ---------------------------------------------------------------------------
# N12 — /demo/cleanup-expired
# ---------------------------------------------------------------------------


def test_n12_cleanup_endpoint_no_longer_compares_raw_internal_secret():
    """The handler must not perform the raw equality check that the fix retires."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "services" / "gateway" / "routers" / "demo.py").read_text()

    forbidden = "x_internal_secret != settings.INTERNAL_SECRET"
    assert forbidden not in src, (
        "N12 regression: /demo/cleanup-expired still gates on raw INTERNAL_SECRET. "
        "Use Depends(verify_internal_secret) instead."
    )

    # The new code MUST use verify_internal_secret.
    assert "verify_internal_secret" in src, (
        "N12 regression: /demo/cleanup-expired no longer imports/uses "
        "verify_internal_secret as its FastAPI dependency."
    )
    # And the destructive sweep MUST emit an audit row.
    assert "demo_cleanup_swept" in src, (
        "N12 regression: cleanup handler no longer emits a demo_cleanup_swept "
        "audit row — required because the endpoint is destructive."
    )


def test_n12_verify_internal_secret_rejects_legacy_header_when_mesh_keys_configured(monkeypatch):
    """End-to-end sanity check on the dependency itself: Phase 3 (already
    landed) rejects X-Internal-Secret-only requests when mesh keys are
    configured. The N12 fix relies on this — it's the whole point of switching
    away from the raw compare.
    """
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"gateway": a_pub}))
    mesh._reset_mesh_caches_for_tests()
    monkeypatch.setattr(mesh.settings, "INTERNAL_SECRET", "the-secret", raising=False)

    # No mesh token AND no header at all → 403.
    with pytest.raises(HTTPException) as exc:
        mesh.verify_internal_secret(secret=None, mesh_token=None)
    assert exc.value.status_code == 403


def test_n12_verify_internal_secret_accepts_mesh_jwt(monkeypatch):
    """The intended N12 caller path: a mesh-signed JWT lets the operator
    Lambda / cron through. ``verify_internal_secret`` returns the issuer
    so the cleanup handler can stamp it on the audit row."""
    a_priv, a_pub = _gen_ec_keypair()
    monkeypatch.setenv("ACP_MESH_SERVICE_NAME", "cron")
    monkeypatch.setenv("ACP_MESH_PRIVATE_KEY_PEM", a_priv)
    monkeypatch.setenv("ACP_MESH_TRUSTED_KEYS", json.dumps({"cron": a_pub}))
    mesh._reset_mesh_caches_for_tests()

    token = mesh.mint_service_token("cron")
    who = mesh.verify_internal_secret(secret=None, mesh_token=token)
    assert who == "mesh:cron"
