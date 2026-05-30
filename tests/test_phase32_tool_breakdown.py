"""Phase 32 source-contract tests — tool-level risk breakdown."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_tool_risk_breakdown ───────────────────────────────────

def test_aggregator_has_tool_breakdown_method():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_tool_risk_breakdown" in src


def test_aggregator_tool_breakdown_has_tools_list():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "tools" in snippet


def test_aggregator_tool_breakdown_has_deny_rate():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "deny_rate" in snippet


def test_aggregator_tool_breakdown_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_aggregator_tool_breakdown_has_total_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "total_calls" in snippet


def test_aggregator_tool_breakdown_has_denied_calls():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "denied_calls" in snippet


def test_aggregator_tool_breakdown_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_tool_breakdown_groups_by_tool():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "group_by" in snippet or "AuditLog.tool" in snippet


def test_aggregator_tool_breakdown_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_tool_breakdown_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_tool_breakdown_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_tool_risk_breakdown")
    snippet = src[idx:idx + 3000]
    assert "computed_at" in snippet


# ── router.py: GET /logs/tool-breakdown ─────────────────────────────────────

def test_router_has_tool_breakdown_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "tool-breakdown" in src


def test_router_tool_breakdown_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_tool_risk_breakdown" in src


def test_router_tool_breakdown_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-breakdown")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_router_tool_breakdown_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("tool-breakdown")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_tool_breakdown_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "tool-breakdown" in src


def test_gateway_tool_breakdown_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("tool-breakdown")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/tool-breakdown" in snippet


# ── api.js: getToolBreakdown ─────────────────────────────────────────────────

def test_api_js_has_get_tool_breakdown():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getToolBreakdown" in src


def test_api_js_tool_breakdown_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getToolBreakdown")
    snippet = src[idx:idx + 200]
    assert "tool-breakdown" in snippet


def test_api_js_tool_breakdown_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getToolBreakdown")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: ToolBreakdownTable ──────────────────────────────────

def test_policy_analytics_has_tool_breakdown_table():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "ToolBreakdownTable" in src


def test_policy_analytics_uses_get_tool_breakdown():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getToolBreakdown" in src


def test_policy_analytics_has_tool_breakdown_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "toolBreakdown" in src


def test_policy_analytics_table_shows_deny_rate():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "deny_rate" in src or "Deny Rate" in src


def test_policy_analytics_table_shows_avg_risk():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "avg_risk" in src or "Avg Risk" in src


def test_policy_analytics_table_shows_denied_calls():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "denied_calls" in src or "Denials" in src


def test_policy_analytics_table_shows_total_calls():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "total_calls" in src or "Total Calls" in src


def test_policy_analytics_has_tool_risk_section_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Tool Risk Breakdown" in src or "Tool Risk" in src
