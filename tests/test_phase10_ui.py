"""Phase 10 UI source-contract tests — Playbooks page, Agents profile action, command palette."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Playbooks page ────────────────────────────────────────────────────────────

def test_playbooks_page_exists():
    assert (ROOT / "ui/src/pages/Playbooks.jsx").exists()


def test_playbooks_uses_playbook_service():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "playbookService" in src


def test_playbooks_has_install_from_template():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "Install" in src
    assert "TemplateCard" in src or "template" in src.lower()


def test_playbooks_has_trigger_modal():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "TriggerModal" in src
    assert "trigger" in src.lower()


def test_playbooks_has_runs_modal():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "RunsModal" in src
    assert "getRuns" in src


def test_playbooks_has_toggle_active():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "is_active" in src
    assert "onToggle" in src or "handleToggle" in src


def test_playbooks_in_app_routes():
    src = (ROOT / "ui/src/App.jsx").read_text()
    assert "/playbooks" in src
    assert "Playbooks" in src


def test_playbooks_in_sidebar():
    src = (ROOT / "ui/src/components/Layout/Sidebar.jsx").read_text()
    assert "/playbooks" in src
    assert "BookOpen" in src


def test_playbooks_in_settings():
    src = (ROOT / "ui/src/pages/Settings.jsx").read_text()
    assert "/playbooks" in src
    assert "BookOpen" in src


def test_playbooks_in_command_palette():
    src = (ROOT / "ui/src/components/Common/CommandPalette.jsx").read_text()
    assert "playbooks" in src


# ── api.js playbookService.getStats ──────────────────────────────────────────

def test_playbook_service_has_get_stats():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getStats" in src
    assert "/playbooks/stats" in src


# ── Agents page: View Profile action ─────────────────────────────────────────

def test_agents_has_view_profile_button():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "/profile" in src
    assert "View profile" in src or "profile" in src.lower()


def test_agents_imports_user_icon():
    src = (ROOT / "ui/src/pages/Agents.jsx").read_text()
    assert "User" in src


# ── CommandPalette has new pages ─────────────────────────────────────────────

def test_command_palette_has_users_and_policy_sim():
    src = (ROOT / "ui/src/components/Common/CommandPalette.jsx").read_text()
    assert "users" in src
    assert "policy-sim" in src
