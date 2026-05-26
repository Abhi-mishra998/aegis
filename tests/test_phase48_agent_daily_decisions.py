"""Phase 48 source-contract tests — agent daily decision volume."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_daily_decisions ─────────────────────────────────

def test_aggregator_has_agent_daily_decisions():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_daily_decisions" in src


def test_aggregator_agent_daily_decisions_accepts_agent_id():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 500]
    assert "agent_id" in snippet


def test_aggregator_agent_daily_decisions_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_agent_daily_decisions_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_agent_daily_decisions_groups_by_day():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2000]
    assert "date_trunc" in snippet or "day" in snippet


def test_aggregator_agent_daily_decisions_has_allow():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2000]
    assert "allow" in snippet


def test_aggregator_agent_daily_decisions_has_deny():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2000]
    assert "deny" in snippet


def test_aggregator_agent_daily_decisions_has_total():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2000]
    assert "total" in snippet


def test_aggregator_agent_daily_decisions_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2500]
    assert "range(days)" in snippet or "timedelta" in snippet


def test_aggregator_agent_daily_decisions_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_agent_daily_decisions_has_total_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2500]
    assert "total_calls" in snippet


def test_aggregator_agent_daily_decisions_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_daily_decisions")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/agent-daily-decisions/{agent_id} ────────────────────

def test_router_has_agent_daily_decisions_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "agent-daily-decisions" in src


def test_router_agent_daily_decisions_has_agent_id_path():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("agent-daily-decisions")
    snippet = src[idx:idx + 400]
    assert "agent_id" in snippet


def test_router_agent_daily_decisions_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_daily_decisions" in src


def test_router_agent_daily_decisions_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("agent-daily-decisions")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_agent_daily_decisions_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "agent-daily-decisions" in src


def test_gateway_agent_daily_decisions_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("agent-daily-decisions")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/agent-daily-decisions" in snippet


# ── api.js: getAgentDailyDecisions ───────────────────────────────────────────

def test_api_js_has_get_agent_daily_decisions():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAgentDailyDecisions" in src


def test_api_js_agent_daily_decisions_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentDailyDecisions")
    snippet = src[idx:idx + 200]
    assert "agent-daily-decisions" in snippet


def test_api_js_agent_daily_decisions_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentDailyDecisions")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── AgentProfile.jsx: AgentDailyDecisionsChart ───────────────────────────────

def test_agent_profile_has_daily_decisions_chart():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "AgentDailyDecisionsChart" in src


def test_agent_profile_uses_get_agent_daily_decisions():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getAgentDailyDecisions" in src


def test_agent_profile_has_daily_decisions_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "dailyDecisions" in src


def test_agent_profile_chart_shows_allow():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentDailyDecisionsChart")
    snippet = src[idx:idx + 1200]
    assert "allow" in snippet


def test_agent_profile_chart_shows_deny():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentDailyDecisionsChart")
    snippet = src[idx:idx + 1200]
    assert "deny" in snippet


def test_agent_profile_chart_uses_bar_chart():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentDailyDecisionsChart")
    snippet = src[idx:idx + 1200]
    assert "BarChart" in snippet or "Bar" in snippet


def test_agent_profile_has_daily_decisions_heading():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Daily Decision" in src or "Decision Volume" in src
