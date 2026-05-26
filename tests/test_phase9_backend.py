"""
Phase 9 backend source-contract tests.

These tests verify file-level contracts only — no imports, no running server.
They check that all required endpoints, proxy routes, and identity service
functions exist in the expected source files with the correct patterns.
"""
from __future__ import annotations

from pathlib import Path

ROOT     = Path(__file__).parent.parent
GATEWAY  = ROOT / "services/gateway/main.py"
IDENTITY = ROOT / "services/identity/router.py"


# ── helpers ────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# 1. Gateway has GET /security/posture endpoint
# ─────────────────────────────────────────────────────────────

def test_gateway_has_security_posture_route():
    src = _read(GATEWAY)
    assert '/security/posture' in src, \
        "GET /security/posture route not found in services/gateway/main.py"


def test_gateway_security_posture_function_defined():
    src = _read(GATEWAY)
    assert 'get_security_posture' in src, \
        "get_security_posture function not found in services/gateway/main.py"


# ─────────────────────────────────────────────────────────────
# 2. Gateway /security/posture calls transparency/roots and logs/verify
# ─────────────────────────────────────────────────────────────

def test_gateway_security_posture_calls_transparency_roots():
    src = _read(GATEWAY)
    assert '/transparency/roots' in src, \
        "security posture does not call /transparency/roots in services/gateway/main.py"


def test_gateway_security_posture_calls_logs_verify():
    src = _read(GATEWAY)
    assert '/logs/verify' in src, \
        "security posture does not call /logs/verify in services/gateway/main.py"


# ─────────────────────────────────────────────────────────────
# 3. Gateway /security/posture returns structured response fields
# ─────────────────────────────────────────────────────────────

def test_gateway_security_posture_returns_posture_score():
    src = _read(GATEWAY)
    assert 'posture_score' in src, \
        "posture_score field missing from security posture response in gateway"


def test_gateway_security_posture_returns_chain_status():
    src = _read(GATEWAY)
    assert 'chain_status' in src, \
        "chain_status field missing from security posture response in gateway"


def test_gateway_security_posture_has_items_list():
    src = _read(GATEWAY)
    assert '"items"' in src or "'items'" in src or '"items": items' in src or "items" in src, \
        "items list missing from security posture response in gateway"


# ─────────────────────────────────────────────────────────────
# 4. Gateway has GET /admin/tenants and GET /admin/tenants/{tenant_id}
# ─────────────────────────────────────────────────────────────

def test_gateway_has_admin_tenants_list_route():
    src = _read(GATEWAY)
    assert '/admin/tenants' in src, \
        "GET /admin/tenants route not found in services/gateway/main.py"


def test_gateway_has_admin_tenants_detail_route():
    src = _read(GATEWAY)
    assert '/admin/tenants/{tenant_id}' in src, \
        "GET /admin/tenants/{tenant_id} route not found in services/gateway/main.py"


# ─────────────────────────────────────────────────────────────
# 5. Gateway admin/tenants proxies to IDENTITY_SERVICE_URL
# ─────────────────────────────────────────────────────────────

def test_gateway_admin_tenants_proxies_to_identity():
    src = _read(GATEWAY)
    assert 'IDENTITY_SERVICE_URL' in src and '/admin/tenants' in src, \
        "admin/tenants proxy does not reference IDENTITY_SERVICE_URL in services/gateway/main.py"


# ─────────────────────────────────────────────────────────────
# 6. Identity service has GET /admin/tenants endpoint
# ─────────────────────────────────────────────────────────────

def test_identity_has_admin_tenants_list_endpoint():
    src = _read(IDENTITY)
    assert '"/admin/tenants"' in src, \
        "GET /admin/tenants endpoint not found in services/identity/router.py"
    assert 'list_admin_tenants' in src, \
        "list_admin_tenants function not found in services/identity/router.py"


# ─────────────────────────────────────────────────────────────
# 7. Identity service has GET /admin/tenants/{tenant_id} endpoint
# ─────────────────────────────────────────────────────────────

def test_identity_has_admin_tenant_detail_endpoint():
    src = _read(IDENTITY)
    assert '"/admin/tenants/{tenant_id}"' in src, \
        "GET /admin/tenants/{tenant_id} endpoint not found in services/identity/router.py"
    assert 'get_admin_tenant' in src, \
        "get_admin_tenant function not found in services/identity/router.py"


# ─────────────────────────────────────────────────────────────
# 8. Identity admin endpoints use verify_internal_secret auth
# ─────────────────────────────────────────────────────────────

def test_identity_admin_tenants_uses_internal_secret():
    src = _read(IDENTITY)
    # Verify verify_internal_secret is referenced near the admin/tenants block
    assert 'verify_internal_secret' in src, \
        "verify_internal_secret not used in services/identity/router.py"
    # Both admin endpoints must declare the dependency
    admin_block = src[src.find('/admin/tenants'):]
    assert 'verify_internal_secret' in admin_block, \
        "verify_internal_secret dependency not found on /admin/tenants endpoint"


# ─────────────────────────────────────────────────────────────
# 9. Identity admin endpoints query the Tenant model
# ─────────────────────────────────────────────────────────────

def test_identity_admin_tenants_queries_tenant_model():
    src = _read(IDENTITY)
    assert 'select(Tenant)' in src, \
        "select(Tenant) not found in services/identity/router.py"
    # Ensure it appears in the admin block specifically
    admin_idx = src.find('/admin/tenants')
    admin_block = src[admin_idx:]
    assert 'select(Tenant)' in admin_block, \
        "select(Tenant) not found in /admin/tenants block of identity router"


# ─────────────────────────────────────────────────────────────
# 10. Identity admin list returns standard {"data": [...]} envelope
# ─────────────────────────────────────────────────────────────

def test_identity_admin_tenants_list_returns_data_envelope():
    src = _read(IDENTITY)
    admin_idx = src.find('list_admin_tenants')
    assert admin_idx != -1, "list_admin_tenants function not found"
    func_block = src[admin_idx:admin_idx + 1500]
    assert '"data"' in func_block or "'data'" in func_block, \
        "data envelope not returned from list_admin_tenants in identity router"


# ─────────────────────────────────────────────────────────────
# 11. Identity admin detail endpoint returns 404 for missing tenant
# ─────────────────────────────────────────────────────────────

def test_identity_admin_tenant_detail_returns_404_on_missing():
    src = _read(IDENTITY)
    detail_idx = src.find('get_admin_tenant')
    assert detail_idx != -1, "get_admin_tenant function not found"
    func_block = src[detail_idx:detail_idx + 1500]
    assert '404' in func_block, \
        "404 not returned for missing tenant in get_admin_tenant"
    assert 'Tenant not found' in func_block or 'not found' in func_block.lower(), \
        "Tenant not found error message missing from get_admin_tenant"


# ─────────────────────────────────────────────────────────────
# 12. Gateway /security/posture gracefully handles sub-call failures
# ─────────────────────────────────────────────────────────────

def test_gateway_security_posture_handles_sub_call_failures():
    src = _read(GATEWAY)
    # Verify try/except blocks around the sub-calls
    posture_idx = src.find('get_security_posture')
    assert posture_idx != -1, "get_security_posture not found"
    func_block = src[posture_idx:posture_idx + 5000]
    assert 'except Exception' in func_block, \
        "get_security_posture does not have except blocks for graceful sub-call failure handling"
    assert '"unknown"' in func_block or "'unknown'" in func_block, \
        "unknown status not returned on sub-call failure in get_security_posture"
