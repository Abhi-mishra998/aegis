"""Phase 14 UI source-contract tests — agent recent decisions, kill-switch history, incident→agent navigation."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── api.js: new auditService methods ─────────────────────────────────────────

def test_audit_service_has_get_agent_logs():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAgentLogs" in src


def test_audit_service_get_agent_logs_passes_agent_id():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "agent_id" in src


def test_audit_service_has_get_kill_switch_history():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getKillSwitchHistory" in src


def test_audit_service_kill_switch_history_filters_by_action():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "action=kill" in src


# ── AgentProfile.jsx: recent decisions feed ───────────────────────────────────

def test_agent_profile_imports_audit_service():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "auditService" in src


def test_agent_profile_fetches_agent_logs():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getAgentLogs" in src


def test_agent_profile_has_recent_logs_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "recentLogs" in src


def test_agent_profile_shows_recent_decisions_section():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Recent Decisions" in src


def test_agent_profile_shows_decision_action():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "log.action" in src


def test_agent_profile_shows_risk_score_per_decision():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "risk_score" in src and "log.tool" in src


# ── Incidents.jsx: agent profile navigation ───────────────────────────────────

def test_incidents_imports_use_navigate():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    assert "useNavigate" in src


def test_incidents_navigate_to_agent_profile():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    assert "/agents/" in src and "profile" in src


def test_incidents_has_view_agent_profile_link():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    assert "Agent Profile" in src


def test_incidents_navigate_uses_incident_agent_id():
    src = (ROOT / "ui/src/pages/Incidents.jsx").read_text()
    assert "incident.agent_id" in src


# ── KillSwitch.jsx: history panel ─────────────────────────────────────────────

def test_kill_switch_imports_audit_service():
    src = (ROOT / "ui/src/pages/KillSwitch.jsx").read_text()
    assert "auditService" in src


def test_kill_switch_has_history_state():
    src = (ROOT / "ui/src/pages/KillSwitch.jsx").read_text()
    assert "history" in src


def test_kill_switch_shows_activation_history_panel():
    src = (ROOT / "ui/src/pages/KillSwitch.jsx").read_text()
    assert "Activation History" in src


def test_kill_switch_fetches_kill_logs():
    src = (ROOT / "ui/src/pages/KillSwitch.jsx").read_text()
    assert "getKillSwitchHistory" in src


def test_kill_switch_history_shows_timestamp():
    src = (ROOT / "ui/src/pages/KillSwitch.jsx").read_text()
    assert "row.timestamp" in src
