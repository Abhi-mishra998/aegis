"""Sprint EH-1 — RBAC matrix integration tests.

Asserts every (path, method, role) cell from the canonical spec at
docs/security/rbac_matrix.md. The implementation under test is
services/gateway/_rbac_map.is_authorized(). When you add a route or
change its required role, add a parametrized row here in the SAME PR.

Why a unit test on the map and not a live HTTP test:
  - The map is the single source of truth at decision time.
  - Live tests would require minting a JWT per role + a real backend
    surface; we already have isolation_test.sh for that.
  - The matrix doc + this file form the audit trail an enterprise
    reviewer can read in one sitting.
"""
from __future__ import annotations

import pytest

from services.gateway._rbac_map import is_authorized


# (path, method, role, expected_allowed)
CASES: list[tuple[str, str, str, bool]] = [
    # /workspace/exit-shadow-mode — OWNER only
    ("/workspace/exit-shadow-mode", "POST", "OWNER",            True),
    ("/workspace/exit-shadow-mode", "POST", "ADMIN",            False),
    ("/workspace/exit-shadow-mode", "POST", "SECURITY_ANALYST", False),
    ("/workspace/exit-shadow-mode", "POST", "DEVELOPER",        False),
    ("/workspace/exit-shadow-mode", "POST", "READ_ONLY",        False),

    # /workspace/system-values — OWNER only on PATCH; GET is min READ_ONLY
    ("/workspace/system-values", "PATCH", "OWNER",     True),
    ("/workspace/system-values", "PATCH", "ADMIN",     False),
    ("/workspace/system-values", "PATCH", "READ_ONLY", False),
    ("/workspace/system-values", "GET",   "READ_ONLY", True),

    # /workspace/slack-config — PUT OWNER+ADMIN
    ("/workspace/slack-config", "PUT", "OWNER",     True),
    ("/workspace/slack-config", "PUT", "ADMIN",     True),
    ("/workspace/slack-config", "PUT", "DEVELOPER", False),

    # /agents POST — OWNER + ADMIN
    ("/agents", "POST", "OWNER",     True),
    ("/agents", "POST", "ADMIN",     True),
    ("/agents", "POST", "DEVELOPER", False),
    ("/agents", "POST", "READ_ONLY", False),

    # /agents DELETE — OWNER only
    ("/agents/abc", "DELETE", "OWNER", True),
    ("/agents/abc", "DELETE", "ADMIN", False),

    # /agents GET — all read roles
    ("/agents",         "GET", "READ_ONLY",        True),
    ("/agents",         "GET", "DEVELOPER",        True),
    ("/agents/abc/permissions", "GET", "READ_ONLY", True),

    # /agents quarantine — SECURITY_ANALYST+
    ("/agents/abc/quarantine", "POST", "SECURITY_ANALYST", True),
    ("/agents/abc/quarantine", "POST", "ADMIN",            True),
    ("/agents/abc/quarantine", "POST", "DEVELOPER",        False),
    ("/agents/abc/quarantine", "POST", "READ_ONLY",        False),

    # /execute — DEVELOPER+
    ("/execute", "POST", "DEVELOPER",        True),
    ("/execute", "POST", "SECURITY_ANALYST", True),
    ("/execute", "POST", "READ_ONLY",        False),

    # /compliance/export — OWNER ONLY (architect's red flag)
    ("/compliance/export", "POST", "OWNER",            True),
    ("/compliance/export", "POST", "ADMIN",            False),
    ("/compliance/export", "POST", "SECURITY_ANALYST", False),
    ("/compliance/export", "POST", "DEVELOPER",        False),
    ("/compliance/export", "POST", "READ_ONLY",        False),

    # /compliance/* read — SECURITY_ANALYST+
    ("/compliance/eu-ai-act", "GET", "SECURITY_ANALYST", True),
    ("/compliance/eu-ai-act", "GET", "DEVELOPER",        False),
    ("/compliance/eu-ai-act", "GET", "READ_ONLY",        False),

    # /forensics — SECURITY_ANALYST+
    ("/forensics/investigation/abc", "GET", "SECURITY_ANALYST", True),
    ("/forensics/investigation/abc", "GET", "DEVELOPER",        False),
    ("/forensics/replay/agt",         "GET", "READ_ONLY",        False),

    # /storylines — SECURITY_ANALYST+
    ("/storylines/inc-1", "GET", "SECURITY_ANALYST", True),
    ("/storylines/inc-1", "GET", "DEVELOPER",        False),

    # /audit/logs read — READ_ONLY+
    ("/audit/logs?limit=5", "GET", "READ_ONLY", True),
    ("/audit/logs/abc",     "GET", "READ_ONLY", True),

    # /audit/logs/export — SECURITY_ANALYST+
    ("/audit/logs/export", "POST", "SECURITY_ANALYST", True),
    ("/audit/logs/export", "POST", "DEVELOPER",        False),

    # /api-keys — OWNER + ADMIN only
    ("/api-keys",      "GET",    "OWNER",     True),
    ("/api-keys",      "GET",    "ADMIN",     True),
    ("/api-keys",      "GET",    "DEVELOPER", False),
    ("/api-keys",      "POST",   "ADMIN",     True),
    ("/api-keys/k1",   "DELETE", "ADMIN",     True),
    ("/api-keys/k1",   "DELETE", "DEVELOPER", False),

    # /webhooks/config — OWNER + ADMIN
    ("/webhooks/config", "GET", "OWNER",     True),
    ("/webhooks/config", "PUT", "DEVELOPER", False),

    # /billing — OWNER ONLY
    ("/billing/invoices",  "GET",  "OWNER",     True),
    ("/billing/invoices",  "GET",  "ADMIN",     False),
    ("/billing/checkout",  "POST", "OWNER",     True),
    ("/billing/checkout",  "POST", "ADMIN",     False),

    # /admin — OWNER ONLY
    ("/admin/tenants", "GET", "OWNER", True),
    ("/admin/tenants", "GET", "ADMIN", False),

    # /kill-switch — OWNER + SECURITY_ANALYST
    ("/kill-switch", "POST", "OWNER",            True),
    ("/kill-switch", "POST", "SECURITY_ANALYST", True),
    ("/kill-switch", "POST", "ADMIN",            False),  # NOT in allow-list
    ("/kill-switch", "POST", "DEVELOPER",        False),

    # /autonomy/contracts — ADMIN+ for writes, READ_ONLY+ for reads
    ("/autonomy/contracts",      "POST",   "ADMIN",            True),
    ("/autonomy/contracts/abc",  "DELETE", "ADMIN",            True),
    ("/autonomy/contracts/abc",  "DELETE", "SECURITY_ANALYST", False),
    ("/autonomy/contracts",      "GET",    "READ_ONLY",        True),

    # /autonomy/overrides — SECURITY_ANALYST+
    ("/autonomy/overrides",      "GET",  "SECURITY_ANALYST", True),
    ("/autonomy/overrides",      "POST", "SECURITY_ANALYST", True),
    ("/autonomy/overrides",      "GET",  "DEVELOPER",        False),

    # /dashboard/state — READ_ONLY+
    ("/dashboard/state", "GET", "READ_ONLY", True),
    ("/dashboard/state", "GET", "OWNER",     True),

    # /notifications — READ_ONLY+
    ("/notifications/count",       "GET",  "READ_ONLY", True),
    ("/notifications/abc/ack",     "POST", "READ_ONLY", True),

    # Routes NOT in the map should fall through (allow)
    ("/some/unmapped/route", "GET", "READ_ONLY", True),
]


@pytest.mark.parametrize("path,method,role,expected", CASES)
def test_rbac_matrix(path: str, method: str, role: str, expected: bool) -> None:
    allowed, reason = is_authorized(path, method, role)
    assert allowed == expected, (
        f"\n{method} {path} as {role}\n"
        f"  expected: {'ALLOW' if expected else 'DENY'}\n"
        f"  got:      {'ALLOW' if allowed else 'DENY'}\n"
        f"  reason:   {reason}"
    )


def test_unknown_role_denied_on_gated_routes() -> None:
    """An unrecognised role must NOT satisfy a min_role constraint."""
    allowed, _ = is_authorized("/compliance/eu-ai-act", "GET", "PIRATE")
    assert allowed is False


def test_owner_is_strictly_above_admin() -> None:
    """Hierarchy invariant: OWNER >= ADMIN >= SECURITY_ANALYST >= DEVELOPER >= READ_ONLY."""
    from services.gateway._rbac_map import _meets
    assert _meets("OWNER", "READ_ONLY") is True
    assert _meets("ADMIN", "OWNER") is False
    assert _meets("DEVELOPER", "SECURITY_ANALYST") is False
    assert _meets("READ_ONLY", "DEVELOPER") is False
