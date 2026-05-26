"""
Phase 8 backend source-contract tests.

These tests verify file-level contracts only — no imports, no running server.
They check that all required endpoints, gateway proxies, and API client
methods exist in the expected source files.
"""
from __future__ import annotations

from pathlib import Path

COMPLIANCE   = Path(__file__).parent.parent / "services/audit/compliance.py"
GATEWAY      = Path(__file__).parent.parent / "services/gateway/main.py"
IDENTITY     = Path(__file__).parent.parent / "services/identity/router.py"
API_JS       = Path(__file__).parent.parent / "ui/src/services/api.js"


# ── helpers ────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. audit/compliance.py has POST /audit/export endpoint ─────────────────

def test_audit_export_endpoint_in_compliance():
    src = _read(COMPLIANCE)
    assert "audit_export_router" in src, \
        "audit_export_router not found in services/audit/compliance.py"
    assert '@audit_export_router.post("/export")' in src or \
           "audit_export_router.post" in src, \
        "POST /audit/export endpoint not found in services/audit/compliance.py"


# ── 2. gateway has POST /audit/export proxy ────────────────────────────────

def test_gateway_has_audit_export_post_proxy():
    src = _read(GATEWAY)
    assert 'app.post("/audit/export"' in src, \
        'POST /audit/export proxy not found in services/gateway/main.py'


# ── 3. identity/router.py has GET /users endpoint ─────────────────────────

def test_identity_has_get_users_endpoint():
    src = _read(IDENTITY)
    assert '"/users"' in src and ("list_users" in src or "GET" in src), \
        "GET /users endpoint not found in services/identity/router.py"
    assert "list_users" in src, \
        "list_users function not found in services/identity/router.py"


# ── 4. identity/router.py has POST /users/invite endpoint ─────────────────

def test_identity_has_invite_user_endpoint():
    src = _read(IDENTITY)
    assert '"/users/invite"' in src, \
        "POST /users/invite endpoint not found in services/identity/router.py"
    assert "invite_user" in src, \
        "invite_user function not found in services/identity/router.py"


# ── 5. identity/router.py has PATCH /users/{user_id} endpoint ─────────────

def test_identity_has_patch_user_endpoint():
    src = _read(IDENTITY)
    assert '"/users/{user_id}"' in src, \
        "PATCH /users/{user_id} endpoint not found in services/identity/router.py"
    assert "update_user" in src, \
        "update_user function not found in services/identity/router.py"


# ── 6. identity/router.py has DELETE /users/{user_id} endpoint ────────────

def test_identity_has_delete_user_endpoint():
    src = _read(IDENTITY)
    assert "deactivate_user" in src, \
        "DELETE /users/{user_id} (deactivate_user) not found in services/identity/router.py"
    assert "is_active = False" in src or "is_active=False" in src, \
        "Soft-delete (is_active = False) logic not found in services/identity/router.py"


# ── 7. gateway has GET /users proxy ───────────────────────────────────────

def test_gateway_has_get_users_proxy():
    src = _read(GATEWAY)
    assert 'app.get("/users"' in src, \
        'GET /users proxy not found in services/gateway/main.py'


# ── 8. gateway has POST /users/invite proxy ────────────────────────────────

def test_gateway_has_post_users_invite_proxy():
    src = _read(GATEWAY)
    assert 'app.post("/users/invite"' in src, \
        'POST /users/invite proxy not found in services/gateway/main.py'


# ── 9. gateway has PATCH /users proxy ─────────────────────────────────────

def test_gateway_has_patch_users_proxy():
    src = _read(GATEWAY)
    assert 'app.patch("/users/{user_id}"' in src, \
        'PATCH /users/{user_id} proxy not found in services/gateway/main.py'


# ── 10. api.js has auditExportService ─────────────────────────────────────

def test_api_js_has_audit_export_service():
    src = _read(API_JS)
    assert "auditExportService" in src, \
        "auditExportService not found in ui/src/services/api.js"


# ── 11. api.js has userService ────────────────────────────────────────────

def test_api_js_has_user_service():
    src = _read(API_JS)
    assert "userService" in src, \
        "userService not found in ui/src/services/api.js"


# ── 12. api.js userService has invite method ──────────────────────────────

def test_api_js_user_service_has_invite():
    src = _read(API_JS)
    assert "invite:" in src or "invite :" in src, \
        "invite method not found in userService in ui/src/services/api.js"
    # Verify it's actually in the userService block
    user_svc_start = src.find("userService")
    assert user_svc_start != -1, "userService block not found"
    user_svc_block = src[user_svc_start:user_svc_start + 500]
    assert "invite" in user_svc_block, \
        "invite method not found within userService definition"
