"""Phase 44 source-contract tests — tool risk leaderboard."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_tool_risk_leaderboard ─────────────────────────────────

def test_aggregator_has_tool_risk_leaderboard():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_tool_risk_leaderboard" in src


def test_aggregator_tool_risk_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_tool_risk_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_tool_risk_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_tool_risk_groups_by_tool():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 3000]
    assert "group_by" in snippet or "GROUP BY" in snippet


def test_aggregator_tool_risk_has_deny_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 3000]
    assert "deny_count" in snippet


def test_aggregator_tool_risk_has_deny_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 2500]
    assert "deny_rate" in snippet


def test_aggregator_tool_risk_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 2500]
    assert "avg_risk" in snippet


def test_aggregator_tool_risk_has_agent_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 2500]
    assert "agent_count" in snippet


def test_aggregator_tool_risk_returns_tools_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 2500]
    assert '"tools"' in snippet or "'tools'" in snippet


def test_aggregator_tool_risk_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_leaderboard")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/tool-risk ───────────────────────────────────────────

def test_router_has_tool_risk_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "tool-risk" in src


def test_router_tool_risk_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_tool_risk_leaderboard" in src


def test_router_tool_risk_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-risk")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


def test_router_tool_risk_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-risk")
    snippet = src[idx:idx + 400]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_tool_risk_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "tool-risk" in src


def test_gateway_tool_risk_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("tool-risk")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/tool-risk" in snippet


# ── api.js: getToolRisk ──────────────────────────────────────────────────────

def test_api_js_has_get_tool_risk():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getToolRisk" in src


def test_api_js_tool_risk_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getToolRisk")
    snippet = src[idx:idx + 200]
    assert "tool-risk" in snippet


def test_api_js_tool_risk_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getToolRisk")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── RiskEngine.jsx: ToolRiskLeaderboard ──────────────────────────────────────

def test_risk_engine_has_tool_risk_leaderboard():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "ToolRiskLeaderboard" in src


def test_risk_engine_uses_get_tool_risk():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "getToolRisk" in src


def test_risk_engine_has_tool_risk_state():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "toolRisk" in src


def test_risk_engine_leaderboard_shows_tool():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("ToolRiskLeaderboard")
    snippet = src[idx:idx + 1500]
    assert "tool" in snippet


def test_risk_engine_leaderboard_shows_deny_rate():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("ToolRiskLeaderboard")
    snippet = src[idx:idx + 1500]
    assert "deny_rate" in snippet


def test_risk_engine_leaderboard_shows_avg_risk():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("ToolRiskLeaderboard")
    snippet = src[idx:idx + 1500]
    assert "avg_risk" in snippet


def test_risk_engine_leaderboard_shows_bar():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("ToolRiskLeaderboard")
    snippet = src[idx:idx + 1500]
    assert "width" in snippet or "barPct" in snippet or "deny_count" in snippet


def test_risk_engine_has_tool_risk_heading():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "Tool Risk" in src
