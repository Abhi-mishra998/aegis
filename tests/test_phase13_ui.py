"""Phase 13 UI source-contract tests — agent reactivation, incidents badge, dashboard quarantine."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Agents.jsx: reactivation ─────────────────────────────────────────────────

def test_agents_has_reactivate_handler():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "handleReactivate" in src or "reactivate" in src.lower()


def test_agents_reactivate_calls_update_active():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "ACTIVE" in src
    assert "reactivateTarget" in src


def test_agents_reactivate_uses_confirm_dialog():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "Reactivate" in src
    assert src.count("ConfirmDialog") >= 2


def test_agents_reactivate_uses_add_toast():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "reactivated" in src.lower()


def test_agents_imports_rotate_ccw():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "RotateCcw" in src


def test_agents_reactivate_only_for_non_active():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "quarantined" in src and "inactive" in src


# ── Topbar.jsx: open incidents badge ─────────────────────────────────────────

def test_topbar_imports_incident_service():
    src = (ROOT / "ui/src/components/Layout/Topbar.jsx").read_text()
    assert "incidentService" in src


def test_topbar_polls_incident_summary():
    src = (ROOT / "ui/src/components/Layout/Topbar.jsx").read_text()
    assert "getSummary" in src
    assert "openIncidents" in src


def test_topbar_shows_incident_badge():
    src = (ROOT / "ui/src/components/Layout/Topbar.jsx").read_text()
    assert "openIncidents" in src
    assert "AlertTriangle" in src


def test_topbar_navigates_to_incidents():
    src = (ROOT / "ui/src/components/Layout/Topbar.jsx").read_text()
    assert "/incidents" in src


# ── gateway/main.py: dashboard state uses agents/summary ─────────────────────

def test_dashboard_state_uses_agents_summary():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "agents/summary" in src


def test_dashboard_state_includes_quarantined():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("dashboard_state")
    snippet = src[idx:idx + 3000]
    assert "quarantined" in snippet


def test_dashboard_state_includes_high_risk():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("dashboard_state")
    snippet = src[idx:idx + 3000]
    assert "high_risk" in snippet


# ── ExecutiveDashboard.jsx: shows quarantined info ───────────────────────────

def test_executive_dashboard_reads_agent_stats():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "agentStats" in src


def test_executive_dashboard_shows_quarantined():
    src = (ROOT / "ui/src/pages/ExecutiveDashboard.jsx").read_text()
    assert "quarantined" in src
