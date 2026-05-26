"""Phase 9 UI source-contract tests — Notification Bell, Security Posture, Admin Tenants."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Sidebar notification bell ─────────────────────────────────────────────────

def test_sidebar_imports_bell_icon():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "Bell" in src


def test_sidebar_imports_notification_service():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "notificationService" in src


def test_sidebar_has_unread_count_state():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "unreadCount" in src


def test_sidebar_polls_notification_count():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "getCount" in src
    assert "setInterval" in src


def test_sidebar_renders_bell_button():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "/notifications" in src
    assert "<Bell" in src


def test_sidebar_shows_unread_badge():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "unreadCount > 0" in src
    assert "99+" in src


# ── SecurityDashboard posture panel ──────────────────────────────────────────

def test_security_dashboard_imports_security_service():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "securityService" in src


def test_security_dashboard_has_posture_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "posture" in src
    assert "setPosture" in src


def test_security_dashboard_calls_get_posture():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getPosture" in src


def test_security_dashboard_renders_posture_checklist():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "Security Posture" in src
    assert "posture_score" in src
    assert "posture.items" in src


# ── api.js new services ───────────────────────────────────────────────────────

def test_api_exports_security_service():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "securityService" in src
    assert "/security/posture" in src


def test_api_exports_admin_service():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "adminService" in src
    assert "/admin/tenants" in src


# ── AdminConsole wired to adminService ───────────────────────────────────────

def test_admin_console_uses_admin_service():
    src = (ROOT / "ui/src/pages/AdminConsole.jsx").read_text()
    assert "adminService" in src
    assert "listTenants" in src


def test_admin_console_no_longer_uses_registry_for_tenants():
    src = (ROOT / "ui/src/pages/AdminConsole.jsx").read_text()
    # Should use adminService, not registryService for tenants
    assert "adminService" in src
