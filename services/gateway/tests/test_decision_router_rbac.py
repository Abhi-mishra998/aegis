"""Unit tests — gateway kill-switch proxy must enforce ADMIN/SECURITY role.

N9 fix (brutal-review 2026-06-21): the GET kill-switch proxy at
services/gateway/routers/decision.py was relying entirely on the
downstream decision service for RBAC. A READ_ONLY or DEVELOPER user
in the same tenant could poll the gateway proxy to detect when the
security team activates an emergency lockdown — letting an attacker
time exfil operations around active defenses.

These tests pin the defense-in-depth gate at the gateway boundary so
the request is rejected before it ever leaves the perimeter.
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Bootstrap minimum env so sdk.common.config.ACPSettings() can instantiate
# at import time. Matches the pattern in test_dashboard_router.py.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "a" * 64)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")

import pytest  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from services.gateway.routers import decision as dec  # noqa: E402


def _make_request(
    role: str,
    tenant_id: str,
    *,
    jwt_tenant: str | None = None,
) -> SimpleNamespace:
    """Minimal Request stand-in for the decision router handlers.

    The handlers read request.state.role (via require_admin_role) and
    request.state.jwt_claims["tenant_id"] (via assert_path_tenant_matches_jwt).
    """
    client = MagicMock()
    client.get = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "disengaged", "tenant_id": tenant_id},
        headers={"content-type": "application/json"},
        content=b'{"status": "disengaged"}',
    ))
    client.post = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "engaged"},
        headers={"content-type": "application/json"},
        content=b'{"status": "engaged"}',
    ))
    client.delete = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"status": "disengaged"},
        headers={"content-type": "application/json"},
        content=b'{"status": "disengaged"}',
    ))

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(client=client)),
        headers={},
        cookies={},
        url=SimpleNamespace(path=f"/decision/kill-switch/{tenant_id}"),
        state=SimpleNamespace(
            role=role,
            tenant_id=tenant_id,
            agent_id=None,
            actor="unit-test",
            jwt_claims={"tenant_id": jwt_tenant if jwt_tenant is not None else tenant_id},
        ),
    )


# ---------------------------------------------------------------------------
# GET /decision/kill-switch/{tenant_id} — role gating
# ---------------------------------------------------------------------------


def test_kill_switch_get_rejects_read_only_role():
    """READ_ONLY caller polling the GET proxy must receive 403."""
    req = _make_request(role="READ_ONLY", tenant_id="t-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.get_kill_switch_status("t-1", req))
    assert exc.value.status_code == 403
    assert "Admin role required" in exc.value.detail


def test_kill_switch_get_rejects_developer_role():
    """DEVELOPER caller (write-execute, no admin) must receive 403."""
    req = _make_request(role="DEVELOPER", tenant_id="t-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.get_kill_switch_status("t-1", req))
    assert exc.value.status_code == 403


def test_kill_switch_get_rejects_agent_role():
    """API-key agent role must receive 403 (operations role only)."""
    req = _make_request(role="agent", tenant_id="t-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.get_kill_switch_status("t-1", req))
    assert exc.value.status_code == 403


def test_kill_switch_get_rejects_empty_role():
    """Missing role attribute (unauthenticated edge case) must 403."""
    req = _make_request(role="", tenant_id="t-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.get_kill_switch_status("t-1", req))
    assert exc.value.status_code == 403


def test_kill_switch_get_allows_security_analyst():
    """SECURITY_ANALYST is the canonical operations role — must pass."""
    req = _make_request(role="SECURITY_ANALYST", tenant_id="t-1")
    asyncio.run(dec.get_kill_switch_status("t-1", req))
    # No exception = pass. The handler returned the upstream passthrough.
    req.app.state.client.get.assert_called_once()


def test_kill_switch_get_allows_admin():
    """ADMIN role must pass."""
    req = _make_request(role="ADMIN", tenant_id="t-1")
    asyncio.run(dec.get_kill_switch_status("t-1", req))
    req.app.state.client.get.assert_called_once()


def test_kill_switch_get_allows_owner():
    """OWNER (top tier) must pass."""
    req = _make_request(role="OWNER", tenant_id="t-1")
    asyncio.run(dec.get_kill_switch_status("t-1", req))
    req.app.state.client.get.assert_called_once()


def test_kill_switch_get_allows_legacy_security():
    """Legacy SECURITY role name must still pass for pre-Sprint-1 JWTs."""
    req = _make_request(role="SECURITY", tenant_id="t-1")
    asyncio.run(dec.get_kill_switch_status("t-1", req))
    req.app.state.client.get.assert_called_once()


# ---------------------------------------------------------------------------
# POST /decision/kill-switch/{tenant_id} — role gating + tenant scoping
# ---------------------------------------------------------------------------


def test_kill_switch_post_rejects_read_only_role():
    """Defense-in-depth: middleware blocks non-admin writes but the route
    check is explicit so the contract doesn't depend on middleware order."""
    req = _make_request(role="READ_ONLY", tenant_id="t-1")
    # POST handler reads request.json() — patch it onto the SimpleNamespace.
    req.json = AsyncMock(return_value={"reason": "test"})
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.toggle_kill_switch("t-1", req))
    assert exc.value.status_code == 403


def test_kill_switch_delete_rejects_read_only_role():
    """DELETE handler must also enforce role gating."""
    req = _make_request(role="READ_ONLY", tenant_id="t-1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.disengage_kill_switch("t-1", req))
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Tenant scoping — even an admin in tenant A can't poll tenant B
# ---------------------------------------------------------------------------


def test_kill_switch_get_rejects_cross_tenant_admin():
    """An ADMIN in tenant A reading tenant B's kill-switch must be 403."""
    req = _make_request(role="ADMIN", tenant_id="t-A", jwt_tenant="t-A")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dec.get_kill_switch_status("t-B", req))
    assert exc.value.status_code == 403
    assert "different tenant" in exc.value.detail
