"""Phase 43 source-contract tests — agent tool usage profile."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_tool_usage ──────────────────────────────────────

def test_aggregator_has_agent_tool_usage():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_tool_usage" in src


def test_aggregator_tool_usage_accepts_agent_id():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 500]
    assert "agent_id" in snippet


def test_aggregator_tool_usage_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_tool_usage_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_tool_usage_groups_by_tool():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2000]
    assert "group_by" in snippet or "GROUP BY" in snippet


def test_aggregator_tool_usage_has_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2000]
    assert "calls" in snippet


def test_aggregator_tool_usage_has_deny_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2000]
    assert "deny_count" in snippet


def test_aggregator_tool_usage_has_deny_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2500]
    assert "deny_rate" in snippet


def test_aggregator_tool_usage_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2500]
    assert "avg_risk" in snippet


def test_aggregator_tool_usage_returns_tools_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2500]
    assert '"tools"' in snippet or "'tools'" in snippet


def test_aggregator_tool_usage_returns_total_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2500]
    assert "total_calls" in snippet


def test_aggregator_tool_usage_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_tool_usage")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/tool-usage/{agent_id} ───────────────────────────────

def test_router_has_tool_usage_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "tool-usage" in src


def test_router_tool_usage_has_agent_id_path_param():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-usage")
    snippet = src[idx:idx + 400]
    assert "agent_id" in snippet


def test_router_tool_usage_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_tool_usage" in src


def test_router_tool_usage_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-usage")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ───────────────────────────────────────────────────

def test_gateway_has_tool_usage_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "tool-usage" in src


def test_gateway_tool_usage_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("tool-usage")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/tool-usage" in snippet


# ── api.js: getAgentToolUsage ────────────────────────────────────────────────

def test_api_js_has_get_agent_tool_usage():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAgentToolUsage" in src


def test_api_js_tool_usage_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentToolUsage")
    snippet = src[idx:idx + 200]
    assert "tool-usage" in snippet


def test_api_js_tool_usage_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentToolUsage")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── AgentProfile.jsx: AgentToolUsageTable ────────────────────────────────────

def test_agent_profile_has_tool_usage_table():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "AgentToolUsageTable" in src


def test_agent_profile_uses_get_agent_tool_usage():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getAgentToolUsage" in src


def test_agent_profile_has_tool_usage_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "toolUsage" in src


def test_agent_profile_table_shows_tool():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentToolUsageTable")
    snippet = src[idx:idx + 1500]
    assert "tool" in snippet


def test_agent_profile_table_shows_calls():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentToolUsageTable")
    snippet = src[idx:idx + 1500]
    assert "calls" in snippet


def test_agent_profile_table_shows_deny_rate():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentToolUsageTable")
    snippet = src[idx:idx + 1500]
    assert "deny_rate" in snippet


def test_agent_profile_table_shows_avg_risk():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentToolUsageTable")
    snippet = src[idx:idx + 1500]
    assert "avg_risk" in snippet


def test_agent_profile_has_tool_usage_heading():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Tool Usage" in src
