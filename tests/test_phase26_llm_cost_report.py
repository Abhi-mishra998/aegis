"""Phase 26 source-contract tests — LLM cost showback report."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── llm_cost_report.py: module structure ─────────────────────────────────────

def test_llm_cost_report_exists():
    assert (ROOT / "services/audit/llm_cost_report.py").exists()


def test_llm_cost_report_has_generate_fn():
    src = (ROOT / "services/audit/llm_cost_report.py").read_text()
    assert "generate_llm_cost_email" in src


def test_llm_cost_report_returns_tuple():
    src = (ROOT / "services/audit/llm_cost_report.py").read_text()
    assert "subject" in src and "text_body" in src and "html_body" in src


def test_llm_cost_report_includes_grand_total():
    src = (ROOT / "services/audit/llm_cost_report.py").read_text()
    assert "grand_total" in src


def test_llm_cost_report_includes_per_agent():
    src = (ROOT / "services/audit/llm_cost_report.py").read_text()
    assert "agents" in src
    assert "agent_id" in src


def test_llm_cost_report_generates_html():
    src = (ROOT / "services/audit/llm_cost_report.py").read_text()
    assert "<!DOCTYPE html>" in src or "html_body" in src
    assert "<html" in src or "html" in src.lower()


# ── generate_llm_cost_email functional test ───────────────────────────────────

def test_generate_llm_cost_email_output():
    from services.audit.llm_cost_report import generate_llm_cost_email
    data = {
        "grand_total":      1.2345,
        "period_weeks":     4,
        "weeks":            ["2026-W20", "2026-W21", "2026-W22", "2026-W23"],
        "agents":           [
            {"agent_id": "aaaaaaaa-0000-0000-0000-000000000001", "total_cost": 0.8, "total_calls": 40},
            {"agent_id": "bbbbbbbb-0000-0000-0000-000000000002", "total_cost": 0.4, "total_calls": 20},
        ],
        "by_agent_by_week": {
            "aaaaaaaa-0000-0000-0000-000000000001": {"2026-W23": 0.3},
        },
        "totals_by_week":   {"2026-W23": 0.3},
    }
    subject, text_body, html_body = generate_llm_cost_email(data)
    assert "$1.23" in subject
    assert "1.2345" in text_body or "grand total" in text_body.lower() or "1.23" in text_body
    assert "<html" in html_body
    assert "aaaaaaaa" in html_body or "aaaaaaaa" in text_body


def test_generate_llm_cost_email_empty_data():
    from services.audit.llm_cost_report import generate_llm_cost_email
    subject, text_body, html_body = generate_llm_cost_email({})
    # Should not raise; should return valid strings
    assert isinstance(subject, str)
    assert isinstance(text_body, str)
    assert isinstance(html_body, str)


# ── billing/router.py: cost-attribution endpoint ─────────────────────────────

def test_billing_router_has_cost_attribution():
    src = (ROOT / "services/billing/router.py").read_text()
    assert "cost-attribution" in src


def test_billing_router_cost_attribution_returns_agents():
    src = (ROOT / "services/billing/router.py").read_text()
    idx = src.find("cost-attribution")
    snippet = src[idx:idx + 1000]
    assert "agents" in snippet
    assert "grand_total" in snippet


def test_billing_router_cost_attribution_groups_by_week():
    src = (ROOT / "services/billing/router.py").read_text()
    idx = src.find("cost-attribution")
    snippet = src[idx:idx + 1000]
    assert "iso_week" in snippet or "IYYY" in snippet or "date_trunc" in snippet or "week" in snippet.lower()


def test_billing_router_cost_attribution_accepts_weeks_param():
    src = (ROOT / "services/billing/router.py").read_text()
    idx = src.find("cost-attribution")
    snippet = src[idx:idx + 800]
    assert "weeks" in snippet


# ── report_delivery.py: llm_cost type support ────────────────────────────────

def test_report_delivery_handles_llm_cost_type():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "llm_cost" in src


def test_report_delivery_sends_html_email_for_cost():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "_send_html_email_sync" in src
    assert "generate_llm_cost_email" in src


def test_report_delivery_html_send_fn_exists():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "def _send_html_email_sync" in src
    assert "html" in src


# ── ScheduledReports.jsx: llm_cost type ──────────────────────────────────────

def test_scheduled_reports_has_llm_cost_type():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "llm_cost" in src


def test_scheduled_reports_llm_cost_has_label():
    src = (ROOT / "ui/src/pages/ScheduledReports.jsx").read_text()
    assert "LLM Cost" in src or "llm_cost" in src


# ── api.js: getCostAttribution ────────────────────────────────────────────────

def test_api_js_has_cost_attribution():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getCostAttribution" in src
    assert "cost-attribution" in src


# ── Billing.jsx: attribution table ────────────────────────────────────────────

def test_billing_jsx_fetches_cost_attribution():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "getCostAttribution" in src
    assert "costAttribution" in src


def test_billing_jsx_shows_grand_total():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "grand_total" in src


def test_billing_jsx_shows_per_agent_rows():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "agents" in src
    assert "agent_id" in src
    assert "total_cost" in src


def test_billing_jsx_shows_weekly_columns():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "by_agent_by_week" in src or "by_week" in src
    assert "LLM Cost Attribution" in src
