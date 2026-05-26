"""Source-contract tests for Phase 5 UI pages."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI   = ROOT / "ui/src"

def _r(p): return (ROOT / p).read_text()

def test_scheduled_reports_page_exists():
    assert (UI / "pages/ScheduledReports.jsx").exists()

def test_scheduled_reports_imports_service():
    assert "scheduledReportsService" in _r("ui/src/pages/ScheduledReports.jsx")

def test_scheduled_reports_has_create_form():
    src = _r("ui/src/pages/ScheduledReports.jsx")
    assert "report_type" in src and "schedule" in src and "recipients" in src

def test_scheduled_reports_has_run_now():
    assert "runNow" in _r("ui/src/pages/ScheduledReports.jsx")

def test_threat_intel_page_exists():
    assert (UI / "pages/ThreatIntel.jsx").exists()

def test_threat_intel_imports_service():
    assert "threatIntelService" in _r("ui/src/pages/ThreatIntel.jsx")

def test_threat_intel_handles_ip_and_domain():
    src = _r("ui/src/pages/ThreatIntel.jsx")
    assert "enrichIp" in src and "enrichDomain" in src

def test_threat_intel_has_score_badge():
    assert "ScoreBadge" in _r("ui/src/pages/ThreatIntel.jsx")

def test_quota_management_page_exists():
    assert (UI / "pages/QuotaManagement.jsx").exists()

def test_quota_management_imports_tenant_service():
    assert "tenantService" in _r("ui/src/pages/QuotaManagement.jsx")

def test_quota_management_shows_progress_bars():
    assert "ProgressBar" in _r("ui/src/pages/QuotaManagement.jsx")

def test_all_pages_in_app_routes():
    src = _r("ui/src/App.jsx")
    assert "ScheduledReports" in src
    assert "ThreatIntel" in src
    assert "QuotaManagement" in src

def test_settings_hub_links_phase5_pages():
    src = _r("ui/src/pages/Settings.jsx")
    assert "/scheduled-reports" in src
    assert "/threat-intel" in src
    assert "/quota" in src

def test_api_js_has_phase5_services():
    src = _r("ui/src/services/api.js")
    assert "scheduledReportsService" in src
    assert "threatIntelService" in src
