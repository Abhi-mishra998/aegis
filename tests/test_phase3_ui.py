"""Source-contract tests for Phase 3 UI pages (no server needed)."""
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
UI = ROOT / "ui/src"


def _read(path):
    return (ROOT / path).read_text()


def test_webhook_settings_page_exists():
    assert (UI / "pages/WebhookSettings.jsx").exists()


def test_webhook_settings_imports_webhook_service():
    src = _read("ui/src/pages/WebhookSettings.jsx")
    assert "webhookService" in src


def test_webhook_settings_has_slack_card():
    src = _read("ui/src/pages/WebhookSettings.jsx")
    assert "Slack" in src and "hooks.slack.com" in src


def test_webhook_settings_has_pagerduty_card():
    src = _read("ui/src/pages/WebhookSettings.jsx")
    assert "PagerDuty" in src


def test_webhook_settings_has_test_buttons():
    src = _read("ui/src/pages/WebhookSettings.jsx")
    assert "testSlack" in src
    assert "testPagerduty" in src


def test_admin_console_page_exists():
    assert (UI / "pages/AdminConsole.jsx").exists()


def test_admin_console_has_kpi_cards():
    src = _read("ui/src/pages/AdminConsole.jsx")
    assert "Total Decisions" in src
    assert "Blocked" in src


def test_admin_console_has_heatmap():
    src = _read("ui/src/pages/AdminConsole.jsx")
    assert "Heatmap" in src or "heatmap" in src


def test_admin_console_has_tenant_table():
    src = _read("ui/src/pages/AdminConsole.jsx")
    assert "Tenant Activity" in src


def test_admin_console_in_app_routes():
    src = _read("ui/src/App.jsx")
    assert "AdminConsole" in src
    assert "/admin" in src


def test_webhook_settings_in_app_routes():
    src = _read("ui/src/App.jsx")
    assert "WebhookSettings" in src
    assert "/webhook-settings" in src


def test_settings_page_links_to_admin_and_webhooks():
    src = _read("ui/src/pages/Settings.jsx")
    assert "/admin" in src
    assert "/webhook-settings" in src


def test_executive_dashboard_has_board_report_button():
    src = _read("ui/src/pages/ExecutiveDashboard.jsx")
    assert "boardReport" in src or "Board Report" in src


def test_api_js_has_webhook_service_methods():
    src = _read("ui/src/services/api.js")
    assert "testSlack" in src
    assert "testPagerduty" in src
    assert "saveConfig" in src
