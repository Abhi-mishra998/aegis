"""Phase 12 UI source-contract tests — Agent quarantine + fleet summary + posture live data."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── api.js: registryService.getSummary ───────────────────────────────────────

def test_registry_service_has_get_summary():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getSummary" in src
    assert "/agents/summary" in src


# ── Agents.jsx: fleet summary endpoint ───────────────────────────────────────

def test_agents_calls_get_summary():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "getSummary" in src


def test_agents_fleet_shows_quarantined():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "quarantined" in src.lower()
    assert "Quarantined" in src


def test_agents_fleet_shows_high_risk():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "high_risk" in src or "High Risk" in src


# ── Agents.jsx: quarantine action ────────────────────────────────────────────

def test_agents_has_quarantine_button():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "Quarantine" in src
    assert "quarantineTarget" in src


def test_agents_quarantine_uses_confirm_dialog():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "ConfirmDialog" in src


def test_agents_quarantine_calls_update_agent():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "updateAgent" in src
    assert "QUARANTINED" in src


def test_agents_quarantine_uses_add_toast():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "addToast" in src


def test_agents_imports_shield_alert():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "ShieldAlert" in src


def test_agents_imports_confirm_dialog():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "ConfirmDialog" in src
    assert "Common/ConfirmDialog" in src


def test_agents_quarantine_skips_already_quarantined():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "quarantined" in src
