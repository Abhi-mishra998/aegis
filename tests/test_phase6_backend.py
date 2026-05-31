"""Phase 6 backend source-contract tests.

These tests verify that all Phase 6 features are present in the codebase
by inspecting source files directly (no running server required).
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _grep(file_path: str, pattern: str) -> bool:
    """Return True if `pattern` appears verbatim in the file."""
    p = Path(file_path)
    if not p.exists():
        return False
    return pattern in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: notifications module exists
# ---------------------------------------------------------------------------

def test_notifications_module_exists():
    module_path = Path(__file__).parent.parent / "services" / "audit" / "notifications.py"
    assert module_path.exists(), f"Missing: {module_path}"


# ---------------------------------------------------------------------------
# Test 2: Notification model is defined
# ---------------------------------------------------------------------------

def test_notification_model_defined():
    module_path = str(
        Path(__file__).parent.parent / "services" / "audit" / "notifications.py"
    )
    assert _grep(module_path, "class Notification"), \
        "Notification SQLAlchemy model not found in notifications.py"
    assert _grep(module_path, "__tablename__"), \
        "Notification model missing __tablename__"


# ---------------------------------------------------------------------------
# Test 3: CRUD functions exist in notifications.py
# ---------------------------------------------------------------------------

def test_notification_crud_functions_exist():
    module_path = str(
        Path(__file__).parent.parent / "services" / "audit" / "notifications.py"
    )
    for fn in ("create_notification", "list_notifications", "mark_read", "mark_all_read", "get_unread_count"):
        assert _grep(module_path, fn), \
            f"Function '{fn}' not found in notifications.py"


# ---------------------------------------------------------------------------
# Test 4: compliance.py has /notifications endpoint definitions
# ---------------------------------------------------------------------------

def test_compliance_router_has_notification_endpoints():
    compliance_path = str(
        Path(__file__).parent.parent / "services" / "audit" / "compliance.py"
    )
    assert _grep(compliance_path, "/notifications"), \
        "No '/notifications' route found in compliance.py"
    # Verify key endpoints
    for pattern in ("read-all", "/count", "notification_id"):
        assert _grep(compliance_path, pattern), \
            f"Pattern '{pattern}' not found in compliance.py"


# ---------------------------------------------------------------------------
# Test 5: gateway/main.py proxies /notifications
# ---------------------------------------------------------------------------

def test_gateway_proxies_notifications():
    # /notifications was extracted out of main.py into the gateway's
    # proxies sub-router in sprint-2.10. Search the union of both files.
    gateway_root = Path(__file__).parent.parent / "services" / "gateway"
    files = [gateway_root / "main.py", gateway_root / "routers" / "proxies.py"]
    src = "\n".join(f.read_text(encoding="utf-8") for f in files if f.exists())
    assert "/notifications" in src, \
        "No '/notifications' proxy found in gateway main.py or routers/proxies.py"
    assert "AUDIT_SERVICE_URL" in src, \
        "AUDIT_SERVICE_URL not referenced for notifications proxy"


# ---------------------------------------------------------------------------
# Test 6: registry router has 'profile' endpoint
# ---------------------------------------------------------------------------

def test_agent_profile_endpoint_in_registry():
    router_path = str(
        Path(__file__).parent.parent / "services" / "registry" / "router.py"
    )
    assert _grep(router_path, "profile"), \
        "No 'profile' route found in registry/router.py"
    assert _grep(router_path, "get_agent_profile"), \
        "Function 'get_agent_profile' not found in registry/router.py"


# ---------------------------------------------------------------------------
# Test 7: gateway proxies agent profile
# ---------------------------------------------------------------------------

def test_gateway_proxies_agent_profile():
    # /agents/{id}/profile extracted from main.py to routers/agents.py in
    # sprint-5; search both files.
    base = Path(__file__).parent.parent / "services" / "gateway"
    src = (
        (base / "main.py").read_text(encoding="utf-8")
        + (base / "routers" / "agents.py").read_text(encoding="utf-8")
    )
    assert "agent_id}/profile" in src, \
        "No 'agent_id}/profile' proxy route found in gateway"


# ---------------------------------------------------------------------------
# Test 8: identity router has sso/config endpoints
# ---------------------------------------------------------------------------

def test_identity_has_sso_config():
    router_path = str(
        Path(__file__).parent.parent / "services" / "identity" / "router.py"
    )
    assert _grep(router_path, "sso/config"), \
        "No 'sso/config' route found in identity/router.py"
    assert _grep(router_path, "sso/config/test"), \
        "No 'sso/config/test' route found in identity/router.py"
    assert _grep(router_path, "_mask_sso_secret"), \
        "Masking function '_mask_sso_secret' not found in identity/router.py"


# ---------------------------------------------------------------------------
# Test 9: gateway proxies sso/config
# ---------------------------------------------------------------------------

def test_gateway_proxies_sso_config():
    gateway_path = str(
        Path(__file__).parent.parent / "services" / "gateway" / "main.py"
    )
    assert _grep(gateway_path, "sso/config"), \
        "No 'sso/config' proxy found in gateway/main.py"
    assert _grep(gateway_path, "IDENTITY_SERVICE_URL"), \
        "IDENTITY_SERVICE_URL not referenced for SSO config proxy"


# ---------------------------------------------------------------------------
# Test 10: api.js has notificationService
# ---------------------------------------------------------------------------

def test_api_js_has_notification_service():
    api_path = str(
        Path(__file__).parent.parent / "ui" / "src" / "services" / "api.js"
    )
    assert _grep(api_path, "notificationService"), \
        "notificationService not found in ui/src/services/api.js"
    for fn in ("markRead", "markAllRead", "getCount"):
        assert _grep(api_path, fn), \
            f"'{fn}' not found in notificationService in api.js"


# ---------------------------------------------------------------------------
# Test 11: api.js has ssoService
# ---------------------------------------------------------------------------

def test_api_js_has_sso_service():
    api_path = str(
        Path(__file__).parent.parent / "ui" / "src" / "services" / "api.js"
    )
    assert _grep(api_path, "ssoService"), \
        "ssoService not found in ui/src/services/api.js"
    for fn in ("getConfig", "saveConfig", "testConfig", "getProviders"):
        assert _grep(api_path, fn), \
            f"'{fn}' not found in ssoService in api.js"


# ---------------------------------------------------------------------------
# Test 12: registryService in api.js has getProfile
# ---------------------------------------------------------------------------

def test_api_js_registry_has_get_profile():
    api_path = str(
        Path(__file__).parent.parent / "ui" / "src" / "services" / "api.js"
    )
    assert _grep(api_path, "getProfile"), \
        "getProfile not found in registryService in api.js"
    assert _grep(api_path, "profile`"), \
        "getProfile does not reference '/profile' endpoint in api.js"
