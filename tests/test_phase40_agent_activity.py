"""Phase 40 source-contract tests — agent activity summary table."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_activity_summary ─────────────────────────────────

def test_aggregator_has_agent_activity_summary():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_activity_summary" in src


def test_aggregator_activity_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_activity_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_activity_groups_by_agent():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "agent_id" in snippet and ("group_by" in snippet or "GROUP BY" in snippet)


def test_aggregator_activity_has_first_seen():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "first_seen" in snippet


def test_aggregator_activity_has_last_seen():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "last_seen" in snippet


def test_aggregator_activity_has_total_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "total_calls" in snippet


def test_aggregator_activity_has_deny_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2500]
    assert "deny_rate" in snippet


def test_aggregator_activity_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "avg_risk" in snippet


def test_aggregator_activity_orders_by_last_seen():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2000]
    assert "last_seen" in snippet and ("order_by" in snippet or "ORDER BY" in snippet)


def test_aggregator_activity_returns_agents_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2500]
    assert '"agents"' in snippet or "'agents'" in snippet


def test_aggregator_activity_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_activity_summary")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/agent-activity ──────────────────────────────────────

def test_router_has_agent_activity_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "agent-activity" in src


def test_router_agent_activity_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_activity_summary" in src


def test_router_agent_activity_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("agent-activity")
    snippet = src[idx:idx + 400]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_agent_activity_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "agent-activity" in src


def test_gateway_agent_activity_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("agent-activity")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/agent-activity" in snippet


# ── api.js: getAgentActivity ─────────────────────────────────────────────────

def test_api_js_has_get_agent_activity():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAgentActivity" in src


def test_api_js_agent_activity_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentActivity")
    snippet = src[idx:idx + 200]
    assert "agent-activity" in snippet


def test_api_js_agent_activity_accepts_limit():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentActivity")
    snippet = src[idx:idx + 200]
    assert "limit" in snippet


# ── SecurityDashboard.jsx: AgentActivityTable ─────────────────────────────────

def test_dashboard_has_agent_activity_table():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "AgentActivityTable" in src


def test_dashboard_uses_get_agent_activity():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getAgentActivity" in src


def test_dashboard_has_agent_activity_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "agentActivity" in src


def test_dashboard_activity_table_shows_first_seen():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("AgentActivityTable")
    snippet = src[idx:idx + 2000]
    assert "first_seen" in snippet or "First Seen" in snippet


def test_dashboard_activity_table_shows_last_seen():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("AgentActivityTable")
    snippet = src[idx:idx + 2000]
    assert "last_seen" in snippet or "Last Seen" in snippet


def test_dashboard_activity_table_shows_deny_rate():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("AgentActivityTable")
    snippet = src[idx:idx + 2000]
    assert "deny_rate" in snippet or "Deny Rate" in snippet


def test_dashboard_activity_table_shows_avg_risk():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("AgentActivityTable")
    snippet = src[idx:idx + 2000]
    assert "avg_risk" in snippet or "Avg Risk" in snippet


def test_dashboard_activity_table_links_to_forensics():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("AgentActivityTable")
    snippet = src[idx:idx + 2000]
    assert "forensics" in snippet or "navigate" in snippet


def test_dashboard_has_agent_activity_heading():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "Agent Activity" in src or "Agent Registry" in src
