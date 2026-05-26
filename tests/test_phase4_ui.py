"""Source-contract tests for Phase 4 UI pages (SIEM Settings + Policy Analytics)."""
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
UI   = ROOT / "ui/src"


def _read(path):
    return (ROOT / path).read_text()


def test_siem_settings_page_exists():
    assert (UI / "pages/SiemSettings.jsx").exists()


def test_siem_settings_imports_siem_service():
    src = _read("ui/src/pages/SiemSettings.jsx")
    assert "siemService" in src


def test_siem_settings_has_splunk_card():
    src = _read("ui/src/pages/SiemSettings.jsx")
    assert "Splunk" in src


def test_siem_settings_has_datadog_card():
    src = _read("ui/src/pages/SiemSettings.jsx")
    assert "Datadog" in src


def test_siem_settings_has_manual_push():
    src = _read("ui/src/pages/SiemSettings.jsx")
    assert "push" in src.lower() and "Push" in src


def test_policy_analytics_page_exists():
    assert (UI / "pages/PolicyAnalytics.jsx").exists()


def test_policy_analytics_has_charts():
    src = _read("ui/src/pages/PolicyAnalytics.jsx")
    assert "BarChart" in src


def test_policy_analytics_has_kpi_cards():
    src = _read("ui/src/pages/PolicyAnalytics.jsx")
    assert "Active Policies" in src
    assert "Unused Policies" in src


def test_siem_in_app_routes():
    src = _read("ui/src/App.jsx")
    assert "SiemSettings" in src
    assert "/siem" in src


def test_policy_analytics_in_app_routes():
    src = _read("ui/src/App.jsx")
    assert "PolicyAnalytics" in src
    assert "/policy-analytics" in src


def test_settings_page_links_siem_and_policy_analytics():
    src = _read("ui/src/pages/Settings.jsx")
    assert "/siem" in src
    assert "/policy-analytics" in src


def test_api_js_has_siem_service():
    src = _read("ui/src/services/api.js")
    assert "siemService" in src
    assert "testSplunk" in src
    assert "testDatadog" in src
