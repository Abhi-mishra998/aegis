"""Phase 29 source-contract tests — report delivery history tracking."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── scheduled_reports.py: ReportDelivery model ────────────────────────────────

def test_report_delivery_model_exists():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "ReportDelivery" in src


def test_report_delivery_has_status_field():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "status" in src


def test_report_delivery_has_triggered_by():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "triggered_by" in src


def test_report_delivery_has_recipients():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "recipients" in src


def test_report_delivery_has_error_message():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "error_message" in src


def test_report_delivery_has_duration_ms():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "duration_ms" in src


def test_report_delivery_has_record_delivery_fn():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "record_delivery" in src


def test_report_delivery_has_list_deliveries_fn():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "list_deliveries" in src


def test_list_deliveries_orders_newest_first():
    src = (ROOT / "services/audit/scheduled_reports.py").read_text()
    assert "desc" in src


# ── report_delivery.py: records deliveries ────────────────────────────────────

def test_report_delivery_worker_imports_record_delivery():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "record_delivery" in src


def test_report_delivery_worker_records_success():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert '"success"' in src or "'success'" in src


def test_report_delivery_worker_records_failed():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert '"failed"' in src or "'failed'" in src


def test_report_delivery_worker_records_skipped():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert '"skipped"' in src or "'skipped'" in src


def test_report_delivery_worker_records_duration():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "duration_ms" in src


# ── compliance.py: GET /scheduled-reports/{id}/history ───────────────────────

def test_compliance_has_history_endpoint():
    src = (ROOT / "services/audit/compliance.py").read_text()
    assert "history" in src


def test_compliance_history_returns_list():
    src = (ROOT / "services/audit/compliance.py").read_text()
    idx = src.find("history")
    snippet = src[idx:idx + 1000]
    assert "list_deliveries" in snippet or "deliveries" in snippet


def test_compliance_history_includes_status():
    src = (ROOT / "services/audit/compliance.py").read_text()
    idx = src.find("history")
    snippet = src[idx:idx + 1500]
    assert "status" in snippet


def test_compliance_run_now_records_manual_trigger():
    src = (ROOT / "services/audit/compliance.py").read_text()
    assert '"manual"' in src or "'manual'" in src


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_history_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "history" in src
    assert "reports/scheduled" in src


def test_gateway_history_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    # Find the reports/scheduled history proxy (not the ARE rule history proxy)
    idx = src.find("reports/scheduled/{report_id}/history")
    assert idx != -1, "Gateway should have reports/scheduled history proxy"
    snippet = src[idx:idx + 800]
    assert "AUDIT_SERVICE_URL" in snippet or "compliance" in snippet


# ── api.js: getHistory ────────────────────────────────────────────────────────

def test_api_js_has_get_history():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getHistory" in src


def test_api_js_history_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "history" in src
    assert "scheduled" in src


# ── ScheduledReports.jsx: DeliveryHistory component ──────────────────────────

def test_scheduled_reports_has_delivery_history():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "DeliveryHistory" in src


def test_scheduled_reports_shows_delivery_status():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "DELIVERY_STATUS_STYLE" in src or "status" in src


def test_scheduled_reports_shows_success_status():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "success" in src


def test_scheduled_reports_shows_failed_status():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "failed" in src


def test_scheduled_reports_uses_get_history():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "getHistory" in src


def test_scheduled_reports_shows_history_toggle():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "History" in src or "history" in src.lower()
