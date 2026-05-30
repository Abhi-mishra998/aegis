"""Phase 46 source-contract tests — daily active agents trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_daily_active_agents ───────────────────────────────────

def test_aggregator_has_daily_active_agents():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_daily_active_agents" in src


def test_aggregator_daily_active_agents_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_daily_active_agents_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_daily_active_agents_counts_distinct():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 3000]
    assert "distinct" in snippet or "DISTINCT" in snippet


def test_aggregator_daily_active_agents_groups_by_day():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 3000]
    assert "date_trunc" in snippet or "day" in snippet


def test_aggregator_daily_active_agents_has_active_agents_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 3000]
    assert "active_agents" in snippet


def test_aggregator_daily_active_agents_has_total_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 3000]
    assert "total_calls" in snippet


def test_aggregator_daily_active_agents_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 2500]
    assert "range(days)" in snippet or "timedelta" in snippet


def test_aggregator_daily_active_agents_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_daily_active_agents_has_peak_agents():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 2500]
    assert "peak_agents" in snippet


def test_aggregator_daily_active_agents_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_daily_active_agents")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/daily-active-agents ─────────────────────────────────

def test_router_has_daily_active_agents_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "daily-active-agents" in src


def test_router_daily_active_agents_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_daily_active_agents" in src


def test_router_daily_active_agents_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("daily-active-agents")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_daily_active_agents_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "daily-active-agents" in src


def test_gateway_daily_active_agents_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("daily-active-agents")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/daily-active-agents" in snippet


# ── api.js: getDailyActiveAgents ─────────────────────────────────────────────

def test_api_js_has_get_daily_active_agents():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getDailyActiveAgents" in src


def test_api_js_daily_active_agents_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDailyActiveAgents")
    snippet = src[idx:idx + 200]
    assert "daily-active-agents" in snippet


def test_api_js_daily_active_agents_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDailyActiveAgents")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── SecurityDashboard.jsx: DailyActiveAgentsChart ────────────────────────────

def test_security_dashboard_has_daily_active_agents_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "DailyActiveAgentsChart" in src


def test_security_dashboard_uses_get_daily_active_agents():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getDailyActiveAgents" in src


def test_security_dashboard_has_daily_active_agents_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "dailyActiveAgents" in src


def test_security_dashboard_chart_shows_active_agents():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("DailyActiveAgentsChart")
    snippet = src[idx:idx + 1500]
    assert "active_agents" in snippet


def test_security_dashboard_chart_uses_area_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("DailyActiveAgentsChart")
    snippet = src[idx:idx + 1500]
    assert "AreaChart" in snippet or "Area" in snippet


def test_security_dashboard_chart_has_date_axis():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("DailyActiveAgentsChart")
    snippet = src[idx:idx + 1500]
    assert "date" in snippet


def test_security_dashboard_has_daily_active_agents_heading():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "Daily Active Agents" in src or "Active Agents" in src
