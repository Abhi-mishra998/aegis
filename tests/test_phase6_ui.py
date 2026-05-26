"""Source-contract tests for Phase 6 UI pages."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI   = ROOT / "ui/src"

def _r(p): return (ROOT / p).read_text()

def test_agent_profile_page_exists():
    assert (UI / "pages/AgentProfile.jsx").exists()

def test_agent_profile_imports_registry_service():
    assert "registryService" in _r("ui/src/pages/AgentProfile.jsx")

def test_agent_profile_has_risk_trend_chart():
    src = _r("ui/src/pages/AgentProfile.jsx")
    assert "LineChart" in src and "risk_trend" in src

def test_agent_profile_has_drift_detection():
    src = _r("ui/src/pages/AgentProfile.jsx")
    assert "behavioral_drift" in src or "drift" in src

def test_agent_profile_shows_top_tools():
    assert "top_tools" in _r("ui/src/pages/AgentProfile.jsx")

def test_sso_settings_page_exists():
    assert (UI / "pages/SsoSettings.jsx").exists()

def test_sso_settings_imports_sso_service():
    assert "ssoService" in _r("ui/src/pages/SsoSettings.jsx")

def test_sso_settings_has_saml_and_oidc():
    src = _r("ui/src/pages/SsoSettings.jsx")
    assert "saml" in src.lower() and "oidc" in src.lower()

def test_sso_settings_has_connectivity_test():
    assert "testConfig" in _r("ui/src/pages/SsoSettings.jsx")

def test_notifications_page_exists():
    assert (UI / "pages/Notifications.jsx").exists()

def test_notifications_imports_service():
    assert "notificationService" in _r("ui/src/pages/Notifications.jsx")

def test_notifications_has_mark_all_read():
    assert "markAllRead" in _r("ui/src/pages/Notifications.jsx")

def test_all_pages_in_app_routes():
    src = _r("ui/src/App.jsx")
    assert "AgentProfile" in src
    assert "SsoSettings" in src
    assert "Notifications" in src

def test_agent_profile_route_uses_param():
    assert "agents/:id/profile" in _r("ui/src/App.jsx")

def test_settings_hub_has_sso():
    assert "/sso" in _r("ui/src/pages/Settings.jsx")

def test_notification_center_links_to_notifications_page():
    src = _r("ui/src/components/Common/NotificationCenter.jsx")
    assert "/notifications" in src
