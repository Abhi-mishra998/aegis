"""Phase 16 UI source-contract tests — IdentityGraph depth fix, Playground tool suggestions, ExecutiveDashboard threat intel."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── IdentityGraph.jsx: blast radius uses depth state ─────────────────────────

def test_identity_graph_blast_radius_uses_depth_variable():
    src = (ROOT / "ui/src/pages/IdentityGraph.jsx").read_text()
    # Must NOT have the hardcoded literal 3 as the second argument
    assert "getBlastRadius(n.id, depth)" in src


def test_identity_graph_no_hardcoded_depth_in_blast_radius():
    src = (ROOT / "ui/src/pages/IdentityGraph.jsx").read_text()
    assert "getBlastRadius(n.id, 3)" not in src


def test_identity_graph_depth_state_exists():
    src = (ROOT / "ui/src/pages/IdentityGraph.jsx").read_text()
    assert "depth" in src and "setDepth" in src


# ── AgentPlayground.jsx: tool suggestions from permissions ────────────────────

def test_playground_imports_registry_service():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    assert "registryService" in src


def test_playground_fetches_permissions_on_agent_change():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    assert "listPermissions" in src


def test_playground_has_tool_suggestions_state():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    assert "toolSuggestions" in src


def test_playground_renders_suggestion_buttons():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    # Clicking a suggestion button sets the tool value
    assert "setTool(t)" in src


def test_playground_clears_suggestions_when_no_agent():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    assert "setToolSuggestions([])" in src


def test_playground_deduplicates_tool_suggestions():
    src = (ROOT / "ui/src/pages/AgentPlayground.jsx").read_text()
    assert "new Set(tools)" in src


# ── ExecutiveDashboard.jsx: threat intel + insight limit ─────────────────────

def test_exec_dashboard_imports_threat_intel_service():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "threatIntelService" in src


def test_exec_dashboard_has_threat_intel_state():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "threatIntel" in src and "setThreatIntel" in src


def test_exec_dashboard_calls_get_summary():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "threatIntelService.getSummary()" in src


def test_exec_dashboard_shows_threat_intel_panel():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "Threat Intelligence" in src


def test_exec_dashboard_insight_limit_is_five():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    # Both primary and fallback paths use slice(0, 5)
    assert src.count("slice(0, 5)") >= 2


def test_exec_dashboard_insight_limit_not_three():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "slice(0, 3)" not in src
