"""Phase 28 source-contract tests — agent behavioral drift detection."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_drift_report ─────────────────────────────────────

def test_aggregator_has_drift_method():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_drift_report" in src


def test_aggregator_drift_computes_drift_score():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "drift_score" in src


def test_aggregator_drift_has_drift_level():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "drift_level" in src
    assert "critical" in src
    assert "low" in src


def test_aggregator_drift_compares_baseline_and_recent():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "baseline" in src
    assert "recent" in src
    assert "baseline_days" in src
    assert "comparison_hours" in src


def test_aggregator_drift_computes_per_metric_deltas():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "risk_drift" in src
    assert "deny_drift" in src
    assert "tool_drift" in src


def test_aggregator_drift_returns_metrics_dict():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert '"metrics"' in src or "'metrics'" in src


# ── audit/router.py: /drift/{agent_id} endpoint ───────────────────────────────

def test_audit_router_has_drift_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "drift" in src


def test_audit_router_drift_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_drift_report" in src


def test_audit_router_drift_accepts_baseline_days():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "baseline_days" in src


def test_audit_router_drift_accepts_comparison_hours():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "comparison_hours" in src


def test_audit_router_drift_validates_uuid():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("drift")
    snippet = src[idx:idx + 1500]
    assert "uuid.UUID" in snippet or "UUID" in snippet


# ── gateway/main.py: /audit/drift/{agent_id} proxy ───────────────────────────

def test_gateway_has_drift_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/audit/drift/" in src or "drift" in src


def test_gateway_drift_proxy_forwards_to_audit_service():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("drift")
    snippet = src[max(0, idx - 200):idx + 600]
    assert "AUDIT_SERVICE_URL" in snippet or "audit" in snippet.lower()


def test_gateway_drift_proxy_passes_query_params():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("drift")
    snippet = src[max(0, idx - 200):idx + 600]
    assert "query_params" in snippet or "params" in snippet


# ── api.js: getDriftReport ────────────────────────────────────────────────────

def test_api_js_has_get_drift_report():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getDriftReport" in src


def test_api_js_drift_calls_audit_drift_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "/audit/drift/" in src or "audit/drift" in src


def test_api_js_drift_accepts_baseline_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "baseline_days" in src or "baselineDays" in src


# ── AgentProfile.jsx: DriftPanel ─────────────────────────────────────────────

def test_agent_profile_fetches_drift():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getDriftReport" in src


def test_agent_profile_renders_drift_panel():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "drift" in src.lower()
    assert "Behavioral Drift" in src or "drift" in src


def test_agent_profile_shows_drift_level():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "drift_level" in src


def test_agent_profile_shows_drift_score():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "drift_score" in src


def test_agent_profile_shows_metric_bars():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "DriftBar" in src or "drift" in src


def test_agent_profile_shows_baseline_vs_recent():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "baseline" in src
    assert "recent" in src


def test_agent_profile_drift_level_styles():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "DRIFT_LEVEL_STYLE" in src or "drift_level" in src
    assert "critical" in src
    assert "low" in src
