"""Phase 39 source-contract tests — decision outcome stacked trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_decision_trend ────────────────────────────────────────

def test_aggregator_has_decision_trend():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_decision_trend" in src


def test_aggregator_decision_trend_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_decision_trend_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_decision_trend_groups_by_day():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert "date_trunc" in snippet or "day" in snippet


def test_aggregator_decision_trend_has_allow():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert '"allow"' in snippet or "'allow'" in snippet


def test_aggregator_decision_trend_has_deny():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert '"deny"' in snippet or "'deny'" in snippet


def test_aggregator_decision_trend_has_escalate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert '"escalate"' in snippet or "'escalate'" in snippet


def test_aggregator_decision_trend_has_monitor():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert '"monitor"' in snippet or "'monitor'" in snippet


def test_aggregator_decision_trend_has_kill():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert '"kill"' in snippet or "'kill'" in snippet


def test_aggregator_decision_trend_zero_fills_series():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2000]
    assert "range(" in snippet


def test_aggregator_decision_trend_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_decision_trend_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_decision_trend")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/decision-trend ──────────────────────────────────────

def test_router_has_decision_trend_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "decision-trend" in src


def test_router_decision_trend_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_decision_trend" in src


def test_router_decision_trend_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("decision-trend")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_decision_trend_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "decision-trend" in src


def test_gateway_decision_trend_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("decision-trend")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/decision-trend" in snippet


# ── api.js: getDecisionTrend ─────────────────────────────────────────────────

def test_api_js_has_get_decision_trend():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getDecisionTrend" in src


def test_api_js_decision_trend_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDecisionTrend")
    snippet = src[idx:idx + 200]
    assert "decision-trend" in snippet


def test_api_js_decision_trend_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDecisionTrend")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: DecisionTrendChart ───────────────────────────────────

def test_policy_analytics_has_decision_trend_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "DecisionTrendChart" in src


def test_policy_analytics_uses_get_decision_trend():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getDecisionTrend" in src


def test_policy_analytics_has_decision_trend_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "decisionTrend" in src


def test_policy_analytics_imports_area_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "AreaChart" in src


def test_policy_analytics_imports_area():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Area" in src


def test_policy_analytics_decision_chart_has_allow():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    # "allow" lives in DECISION_COLORS defined before DecisionTrendChart
    assert "allow" in src


def test_policy_analytics_decision_chart_has_deny():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "deny" in src


def test_policy_analytics_decision_chart_has_escalate():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "escalate" in src


def test_policy_analytics_decision_chart_uses_stack():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("DecisionTrendChart")
    snippet = src[idx:idx + 2000]
    assert "stackId" in snippet or "stack" in snippet.lower()


def test_policy_analytics_has_decision_trend_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Decision Outcome Trend" in src or "Decision Trend" in src


def test_policy_analytics_decision_chart_has_color_legend():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "DECISION_COLORS" in src
