"""N3 (2026-06-21) — demo workspace blocked-paths defense-in-depth.

Demo workspaces are minted by /demo/spawn-workspace with role=OWNER, so
RBAC alone admits them to every SECURITY_ANALYST+ surface — including
org-wide investigation views that a self-serve sandbox should never read.

The gateway middleware (services/gateway/middleware.py) carries a second
gate that rejects is_demo=True JWTs on:

  /admin/*          (staff-only)
  /forensics/*      (forensic investigations / replay / blast-radius)
  /storylines/*     (incident narrative engine)
  /iag/*            (identity-access graph + per-incident blast-radius)
  /threat-intel/*   (IOC feeds + ingest)

These tests assert each prefix returns 403 to a demo JWT.

Run:
    .venv/bin/pytest tests/test_demo_workspace_blocked_paths.py -v
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from jose import jwt

from sdk.common.config import settings

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

_DEMO_TENANT_ID = uuid.UUID("dddddddd-0000-0000-0000-000000000001")


def _make_demo_token(is_demo: bool = True, role: str = "OWNER") -> str:
    """Mint a JWT that mirrors the /demo/spawn-workspace payload shape.

    See services/gateway/routers/demo.py line 743-754 for the canonical
    structure. The is_demo flag is the only thing that differentiates a
    demo session from a paying-customer OWNER token.
    """
    now = datetime.datetime.now(tz=datetime.UTC)
    exp = now + datetime.timedelta(minutes=15)
    payload = {
        "jti":       f"demo-{uuid.uuid4().hex}",
        "sub":       "demo@example.com",
        "tenant_id": str(_DEMO_TENANT_ID),
        "org_id":    str(_DEMO_TENANT_ID),
        "agent_id":  "00000000-0000-0000-0000-000000000000",
        "role":      role,
        "is_demo":   is_demo,
        "typ":       "ACP_ACCESS",
        "iat":       int(now.timestamp()),
        "exp":       int(exp.timestamp()),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANT-LEVEL CHECKS — middleware exports the right tuples
# ─────────────────────────────────────────────────────────────────────────────


def test_demo_blocked_prefixes_constant_covers_all_required_paths() -> None:
    """The middleware tuple must list every prefix the N3 fix promises."""
    from services.gateway.middleware import _DEMO_BLOCKED_PREFIXES

    required = {"/admin/", "/forensics/", "/storylines/", "/iag/", "/threat-intel/"}
    assert required.issubset(set(_DEMO_BLOCKED_PREFIXES)), (
        f"Missing prefixes: {required - set(_DEMO_BLOCKED_PREFIXES)}"
    )


def test_demo_blocked_exact_includes_bare_paths() -> None:
    """Exact-match list must catch the path without a trailing slash too."""
    from services.gateway.middleware import _DEMO_BLOCKED_EXACT

    # /admin is the canonical example from P0-0; the N3 fix preserves the
    # bare-path guard so e.g. GET /admin (no trailing slash) is still
    # blocked even if a future router registers a route at that exact path.
    assert "/admin" in _DEMO_BLOCKED_EXACT


# ─────────────────────────────────────────────────────────────────────────────
# LIVE HARNESS CHECKS — request gets a 403 against the in-process gateway
# ─────────────────────────────────────────────────────────────────────────────

_BLOCKED_PROBE_PATHS = [
    "/admin/tenants",
    "/forensics/investigation/some-agent-id",
    "/forensics/timeline/some-agent-id",
    "/storylines",
    "/storylines/INC-12345",
    "/iag/agents",
    "/iag/agents/some-agent-id",
    "/iag/incidents/INC-12345/blast-radius",
    "/threat-intel/iocs",
    "/threat-intel/feeds",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _BLOCKED_PROBE_PATHS)
async def test_demo_token_blocked_with_403(path: str) -> None:
    """A demo token (is_demo=True) hitting any blocked path returns 403."""
    from tests.harness import harness

    token = _make_demo_token(is_demo=True, role="OWNER")
    resp = await harness.gateway.get(
        path,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   str(_DEMO_TENANT_ID),
            "Content-Type":  "application/json",
        },
    )
    assert resp.status_code == 403, (
        f"\nExpected 403 on {path}, got {resp.status_code}\n"
        f"body: {resp.text[:300]}"
    )
    # The N3 block emits a specific error body; the RBAC denier also
    # returns 403 but with the RBAC reason. Either is a valid block; the
    # important thing is 403, not which guard fired first.
    body = resp.json()
    assert body.get("error") == "Forbidden", (
        f"Expected error=Forbidden body, got: {body}"
    )


@pytest.mark.asyncio
async def test_non_demo_token_not_blocked_by_n3_guard() -> None:
    """A normal (non-demo) OWNER token must NOT be rejected by the N3 guard.

    It may still be rejected by RBAC or by the route handler itself; the
    point of this test is just that the N3 guard does not over-match. We
    probe a path where OWNER is sufficient (/storylines is SECURITY_ANALYST+
    and OWNER >= that). The response must NOT be 403 with the N3 detail
    string.
    """
    from tests.harness import harness

    token = _make_demo_token(is_demo=False, role="OWNER")
    resp = await harness.gateway.get(
        "/storylines",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   str(_DEMO_TENANT_ID),
            "Content-Type":  "application/json",
        },
    )
    if resp.status_code == 403:
        body = resp.json()
        # 403 is acceptable from RBAC or from a route handler, but it
        # must NOT carry the N3-specific detail string.
        assert body.get("detail") != "demo workspaces cannot access this endpoint", (
            "N3 guard fired on a non-demo token — over-matched"
        )
