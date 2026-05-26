"""Phase 17 UI source-contract tests — Billing investigate, Forensics nav, PolicyBuilder tool hints."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Billing.jsx: anomaly investigate navigation ───────────────────────────────

def test_billing_imports_use_navigate():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "useNavigate" in src


def test_billing_calls_use_navigate():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "navigate = useNavigate()" in src


def test_billing_anomaly_has_investigate_button():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "Investigate" in src


def test_billing_investigate_navigates_to_forensics():
    src = (ROOT / "ui/src/pages/Billing.jsx").read_text()
    assert "/forensics?agent=" in src


# ── Forensics.jsx: reasons limit + navigation buttons ────────────────────────

def test_forensics_reasons_limit_is_five():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "slice(0, 5)" in src


def test_forensics_reasons_limit_not_three():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "slice(0, 3)" not in src


def test_forensics_has_view_agent_profile_button():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "View Agent Profile" in src


def test_forensics_navigates_to_agent_profile():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "/agents/" in src and "profile" in src


def test_forensics_has_view_incidents_button():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "View Incidents" in src


def test_forensics_navigates_to_incidents():
    src = (ROOT / "ui/src/pages/Forensics.jsx").read_text()
    assert "/incidents?agent=" in src


# ── PolicyBuilder.jsx: agent tool suggestions ─────────────────────────────────

def test_policy_builder_has_agent_tool_suggestions_state():
    src = (ROOT / "ui/src/pages/PolicyBuilder.jsx").read_text()
    assert "agentToolSuggestions" in src and "setAgentToolSuggestions" in src


def test_policy_builder_fetches_permissions_on_agent_change():
    src = (ROOT / "ui/src/pages/PolicyBuilder.jsx").read_text()
    assert "listPermissions" in src


def test_policy_builder_clears_suggestions_when_no_agent():
    src = (ROOT / "ui/src/pages/PolicyBuilder.jsx").read_text()
    assert "setAgentToolSuggestions([])" in src


def test_policy_builder_passes_tool_suggestions_to_rule_block():
    src = (ROOT / "ui/src/pages/PolicyBuilder.jsx").read_text()
    assert "toolSuggestions={agentToolSuggestions}" in src


def test_policy_builder_shows_chips_when_tool_condition():
    src = (ROOT / "ui/src/pages/PolicyBuilder.jsx").read_text()
    assert "cond.field === 'tool'" in src
