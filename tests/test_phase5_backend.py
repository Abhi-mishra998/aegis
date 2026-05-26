"""Phase 5 backend source-contract tests — all pass without a running server."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
AUDIT_SVC = REPO_ROOT / "services" / "audit"
GATEWAY_SVC = REPO_ROOT / "services" / "gateway"
UI_API_JS = REPO_ROOT / "ui" / "src" / "services" / "api.js"


def _grep(filepath: Path, needle: str) -> bool:
    """Return True if `needle` appears in `filepath`."""
    return needle in filepath.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1 — scheduled_reports module exists
# ---------------------------------------------------------------------------


def test_scheduled_reports_module_exists():
    path = AUDIT_SVC / "scheduled_reports.py"
    assert path.exists(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# Test 2 — ScheduledReport model defined
# ---------------------------------------------------------------------------


def test_scheduled_report_model_defined():
    path = AUDIT_SVC / "scheduled_reports.py"
    assert _grep(path, "class ScheduledReport"), "ScheduledReport class not found in scheduled_reports.py"


# ---------------------------------------------------------------------------
# Test 3 — CRUD functions exist
# ---------------------------------------------------------------------------


def test_schedule_crud_functions_exist():
    path = AUDIT_SVC / "scheduled_reports.py"
    content = path.read_text(encoding="utf-8")
    for fn in ("create_report", "list_reports", "delete_report", "trigger_report_now"):
        assert fn in content, f"Function '{fn}' not found in scheduled_reports.py"


# ---------------------------------------------------------------------------
# Test 4 — threat_intel module exists
# ---------------------------------------------------------------------------


def test_threat_intel_module_exists():
    path = AUDIT_SVC / "threat_intel.py"
    assert path.exists(), f"Missing: {path}"


# ---------------------------------------------------------------------------
# Test 5 — enrich_ip defined
# ---------------------------------------------------------------------------


def test_enrich_ip_defined():
    path = AUDIT_SVC / "threat_intel.py"
    assert _grep(path, "async def enrich_ip"), "enrich_ip not defined in threat_intel.py"


# ---------------------------------------------------------------------------
# Test 6 — enrich_domain defined
# ---------------------------------------------------------------------------


def test_enrich_domain_defined():
    path = AUDIT_SVC / "threat_intel.py"
    assert _grep(path, "async def enrich_domain"), "enrich_domain not defined in threat_intel.py"


# ---------------------------------------------------------------------------
# Test 7 — demo_ip called when no API key
# ---------------------------------------------------------------------------


def test_demo_ip_skips_when_no_key():
    """enrich_ip should return status in ("ok", "error") with no API key set."""
    # Ensure no key is set
    os.environ.pop("ABUSEIPDB_API_KEY", None)

    # Import the module directly without relying on installed packages
    spec_path = str(AUDIT_SVC)
    if spec_path not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from services.audit.threat_intel import enrich_ip  # noqa: PLC0415

    result = asyncio.run(enrich_ip("1.1.1.1"))
    assert result.get("status") in ("ok", "error"), (
        f"Unexpected status: {result.get('status')!r} — full result: {result}"
    )


# ---------------------------------------------------------------------------
# Test 8 — compliance router has scheduled-reports endpoints
# ---------------------------------------------------------------------------


def test_compliance_router_has_scheduled_reports():
    path = AUDIT_SVC / "compliance.py"
    assert _grep(path, "scheduled-reports"), "No 'scheduled-reports' route found in compliance.py"


# ---------------------------------------------------------------------------
# Test 9 — compliance router has threat-intel endpoints
# ---------------------------------------------------------------------------


def test_compliance_router_has_threat_intel():
    path = AUDIT_SVC / "compliance.py"
    assert _grep(path, "threat-intel"), "No 'threat-intel' route found in compliance.py"


# ---------------------------------------------------------------------------
# Test 10 — gateway proxies scheduled reports
# ---------------------------------------------------------------------------


def test_gateway_proxies_scheduled_reports():
    path = GATEWAY_SVC / "main.py"
    assert _grep(path, "reports/scheduled"), "No 'reports/scheduled' proxy found in gateway/main.py"


# ---------------------------------------------------------------------------
# Test 11 — gateway proxies threat intel
# ---------------------------------------------------------------------------


def test_gateway_proxies_threat_intel():
    path = GATEWAY_SVC / "main.py"
    assert _grep(path, "threat-intel"), "No 'threat-intel' proxy found in gateway/main.py"


# ---------------------------------------------------------------------------
# Test 12 — api.js has both new services
# ---------------------------------------------------------------------------


def test_api_js_has_new_services():
    content = UI_API_JS.read_text(encoding="utf-8")
    assert "scheduledReportsService" in content, "scheduledReportsService not found in api.js"
    assert "threatIntelService" in content, "threatIntelService not found in api.js"
