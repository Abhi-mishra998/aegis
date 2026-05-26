"""Phase 31 source-contract tests — per-agent 30-day risk score trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_risk_trend ──────────────────────────────────────

def test_aggregator_has_risk_trend_method():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_risk_trend" in src


def test_aggregator_risk_trend_has_series():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "series" in snippet


def test_aggregator_risk_trend_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "timedelta" in snippet or "range" in snippet


def test_aggregator_risk_trend_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "avg_risk" in snippet


def test_aggregator_risk_trend_has_deny_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "deny_count" in snippet


def test_aggregator_risk_trend_has_allow_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "allow_count" in snippet


def test_aggregator_risk_trend_has_summary():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 3000]
    assert "summary" in snippet


def test_aggregator_risk_trend_summary_has_max_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 3000]
    assert "max_risk" in snippet


def test_aggregator_risk_trend_summary_has_total_denials():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 3000]
    assert "total_denials" in snippet


def test_aggregator_risk_trend_summary_has_active_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 3000]
    assert "active_days" in snippet


def test_aggregator_risk_trend_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "tenant_id" in snippet


def test_aggregator_risk_trend_filters_by_agent():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_risk_trend")
    snippet = src[idx:idx + 1500]
    assert "agent_id" in snippet


# ── router.py: GET /logs/risk-trend/{agent_id} ───────────────────────────────

def test_router_has_risk_trend_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "risk-trend" in src or "risk_trend" in src


def test_router_risk_trend_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_risk_trend" in src


def test_router_risk_trend_accepts_days_param():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("risk-trend")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_router_risk_trend_validates_uuid():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("risk-trend")
    snippet = src[idx:idx + 600]
    assert "uuid.UUID" in snippet or "UUID" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_risk_trend_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "risk-trend" in src


def test_gateway_risk_trend_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("risk-trend")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/risk-trend" in snippet


# ── api.js: getRiskTrend ─────────────────────────────────────────────────────

def test_api_js_has_get_risk_trend():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getRiskTrend" in src


def test_api_js_risk_trend_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskTrend")
    snippet = src[idx:idx + 200]
    assert "risk-trend" in snippet


def test_api_js_risk_trend_accepts_days_param():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskTrend")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── AgentProfile.jsx: RiskTrendChart component ───────────────────────────────

def test_agent_profile_has_risk_trend_chart():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "RiskTrendChart" in src


def test_agent_profile_uses_get_risk_trend():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getRiskTrend" in src


def test_agent_profile_has_trend_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "trend" in src
    assert "setTrend" in src


def test_agent_profile_risk_trend_shows_summary():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "max_risk" in src or "Peak Risk" in src


def test_agent_profile_risk_trend_shows_denials():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "total_denials" in src or "Denials" in src


def test_agent_profile_risk_trend_shows_active_days():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "active_days" in src or "Active Days" in src


def test_agent_profile_risk_trend_chart_uses_svg():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("RiskTrendChart")
    snippet = src[idx:idx + 1500]
    assert "<svg" in snippet or "polyline" in snippet


def test_agent_profile_risk_trend_has_section_heading():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Risk Score Trend" in src or "risk score trend" in src.lower()
