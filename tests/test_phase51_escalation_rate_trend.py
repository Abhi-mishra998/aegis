"""Phase 51 source-contract tests — daily escalation rate trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_escalation_rate_trend ─────────────────────────────────

def test_aggregator_has_escalation_rate_trend():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_escalation_rate_trend" in src


def test_aggregator_escalation_rate_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_escalation_rate_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_escalation_rate_counts_escalate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 3000]
    assert "escalate" in snippet


def test_aggregator_escalation_rate_groups_by_day():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 3000]
    assert "date_trunc" in snippet or "day" in snippet


def test_aggregator_escalation_rate_has_escalation_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert "escalation_rate" in snippet


def test_aggregator_escalation_rate_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert "range(days)" in snippet or "timedelta" in snippet


def test_aggregator_escalation_rate_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_escalation_rate_has_avg_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert "avg_rate" in snippet


def test_aggregator_escalation_rate_has_peak_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert "peak_rate" in snippet


def test_aggregator_escalation_rate_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_escalation_rate_trend")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/escalation-rate-trend ───────────────────────────────

def test_router_has_escalation_rate_trend_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "escalation-rate-trend" in src


def test_router_escalation_rate_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_escalation_rate_trend" in src


def test_router_escalation_rate_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("escalation-rate-trend")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_escalation_rate_trend_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "escalation-rate-trend" in src


def test_gateway_escalation_rate_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("escalation-rate-trend")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/escalation-rate-trend" in snippet


# ── api.js: getEscalationRateTrend ───────────────────────────────────────────

def test_api_js_has_get_escalation_rate_trend():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getEscalationRateTrend" in src


def test_api_js_escalation_rate_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getEscalationRateTrend")
    snippet = src[idx:idx + 200]
    assert "escalation-rate-trend" in snippet


def test_api_js_escalation_rate_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getEscalationRateTrend")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: EscalationRateTrendChart ────────────────────────────

def test_policy_analytics_has_escalation_rate_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "EscalationRateTrendChart" in src


def test_policy_analytics_uses_get_escalation_rate_trend():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getEscalationRateTrend" in src


def test_policy_analytics_has_escalation_rate_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "escalationRateTrend" in src


def test_policy_analytics_chart_shows_escalation_rate():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("EscalationRateTrendChart")
    snippet = src[idx:idx + 1500]
    assert "escalation_rate" in snippet


def test_policy_analytics_chart_uses_line_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("EscalationRateTrendChart")
    snippet = src[idx:idx + 1500]
    assert "LineChart" in snippet


def test_policy_analytics_chart_has_reference_line():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("EscalationRateTrendChart")
    snippet = src[idx:idx + 1500]
    assert "ReferenceLine" in snippet


def test_policy_analytics_shows_avg_and_peak_rate():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "avg_rate" in src and "peak_rate" in src


def test_policy_analytics_has_escalation_rate_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Escalation Rate" in src
