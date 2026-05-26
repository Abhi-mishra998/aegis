"""Phase 20 source-contract tests — XSS token fix: no localStorage reads for acp_token in api.js or UI pages."""
from pathlib import Path

ROOT = Path(__file__).parent.parent

API_JS = (ROOT / "ui/src/services/api.js").read_text()


# ── api.js: no raw acp_token localStorage reads ───────────────────────────────

def test_api_js_no_localstorage_acp_token():
    lines = [ln for ln in API_JS.splitlines() if "acp_token'" in ln or 'acp_token"' in ln]
    # Only the comment and acp_token_expiry variants are allowed — not getItem('acp_token')
    bad = [ln for ln in lines if "getItem" in ln and "acp_token'" in ln and "expiry" not in ln]
    assert bad == [], f"Found localStorage token reads: {bad}"


def test_api_js_has_blob_request_helper():
    assert "blobRequest" in API_JS


def test_blob_request_uses_credentials_include():
    assert 'credentials: "include"' in API_JS or "credentials: 'include'" in API_JS


def test_blob_request_validates_session():
    assert "isSessionValid()" in API_JS
    # blobRequest must call isSessionValid before fetching
    idx = API_JS.find("blobRequest")
    snippet = API_JS[idx:idx + 400]
    assert "isSessionValid" in snippet


def test_blob_request_sets_tenant_header():
    idx = API_JS.find("const blobRequest")
    snippet = API_JS[idx:idx + 500]
    assert "X-Tenant-ID" in snippet


def test_incident_export_uses_blob_request():
    assert "blobRequest(`/incidents/" in API_JS or "blobRequest('/incidents/" in API_JS


def test_compliance_export_uses_blob_request():
    assert "blobRequest(`/compliance/export" in API_JS or "blobRequest('/compliance/export" in API_JS


def test_compliance_board_report_uses_blob_request():
    assert "blobRequest('/compliance/board-report'" in API_JS or 'blobRequest("/compliance/board-report"' in API_JS


# ── Incidents.jsx: no raw localStorage token read ────────────────────────────

def test_incidents_jsx_no_raw_token_read():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    bad_lines = [ln for ln in src.splitlines() if "getItem" in ln and "acp_token'" in ln and "expiry" not in ln]
    assert bad_lines == [], f"Raw token in Incidents.jsx: {bad_lines}"


def test_incidents_jsx_export_uses_service():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    assert "incidentService.exportPdf" in src


# ── All UI pages: no raw acp_token localStorage reads ────────────────────────

def test_no_raw_acp_token_in_ui_pages():
    pages_dir = ROOT / "ui/src/pages"
    offenders = []
    for jsx in pages_dir.glob("*.jsx"):
        src = jsx.read_text()
        bad = [ln.strip() for ln in src.splitlines()
               if "getItem" in ln and "acp_token'" in ln and "expiry" not in ln]
        if bad:
            offenders.append(f"{jsx.name}: {bad[0]}")
    assert offenders == [], f"Raw acp_token reads found: {offenders}"
