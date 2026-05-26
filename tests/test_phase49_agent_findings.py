"""Phase 49 source-contract tests — per-agent finding frequency."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_finding_breakdown ───────────────────────────────

def test_aggregator_has_agent_finding_breakdown():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_finding_breakdown" in src


def test_aggregator_agent_findings_accepts_agent_id():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 500]
    assert "agent_id" in snippet


def test_aggregator_agent_findings_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_agent_findings_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_agent_findings_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_agent_findings_unnests_jsonb():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2000]
    assert "jsonb_array_elements_text" in snippet


def test_aggregator_agent_findings_has_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2000]
    assert "count" in snippet


def test_aggregator_agent_findings_has_pct():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "pct" in snippet


def test_aggregator_agent_findings_returns_findings_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert '"findings"' in snippet or "'findings'" in snippet


def test_aggregator_agent_findings_has_total():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "total" in snippet


def test_aggregator_agent_findings_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/agent-findings/{agent_id} ───────────────────────────

def test_router_has_agent_findings_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "agent-findings" in src


def test_router_agent_findings_has_agent_id_path():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("agent-findings")
    snippet = src[idx:idx + 400]
    assert "agent_id" in snippet


def test_router_agent_findings_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_finding_breakdown" in src


def test_router_agent_findings_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("agent-findings")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_agent_findings_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "agent-findings" in src


def test_gateway_agent_findings_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("agent-findings")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/agent-findings" in snippet


# ── api.js: getAgentFindings ─────────────────────────────────────────────────

def test_api_js_has_get_agent_findings():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAgentFindings" in src


def test_api_js_agent_findings_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentFindings")
    snippet = src[idx:idx + 200]
    assert "agent-findings" in snippet


def test_api_js_agent_findings_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAgentFindings")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── AgentProfile.jsx: AgentFindingFrequency ──────────────────────────────────

def test_agent_profile_has_finding_frequency():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "AgentFindingFrequency" in src


def test_agent_profile_uses_get_agent_findings():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getAgentFindings" in src


def test_agent_profile_has_agent_findings_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "agentFindings" in src


def test_agent_profile_finding_chart_shows_finding():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentFindingFrequency")
    snippet = src[idx:idx + 1500]
    assert "finding" in snippet


def test_agent_profile_finding_chart_shows_count():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentFindingFrequency")
    snippet = src[idx:idx + 1500]
    assert "count" in snippet


def test_agent_profile_finding_chart_shows_pct():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentFindingFrequency")
    snippet = src[idx:idx + 1500]
    assert "pct" in snippet


def test_agent_profile_finding_chart_has_bar():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    idx = src.find("AgentFindingFrequency")
    snippet = src[idx:idx + 1500]
    assert "width" in snippet or "barPct" in snippet or "maxCount" in snippet


def test_agent_profile_has_finding_frequency_heading():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Finding Frequency" in src or "Findings" in src
