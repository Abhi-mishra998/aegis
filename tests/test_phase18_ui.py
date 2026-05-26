"""Phase 18 UI source-contract tests — agent picker, dynamic tool options, SSO providers, notification count."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── PolicySim.jsx: agent picker from useAgents ────────────────────────────────

def test_policy_sim_imports_use_agents():
    src = (ROOT / "ui/src/pages/PolicySim.jsx").read_text()
    assert "useAgents" in src


def test_policy_sim_uses_agents_hook():
    src = (ROOT / "ui/src/pages/PolicySim.jsx").read_text()
    assert "agents } = useAgents()" in src


def test_policy_sim_renders_agent_select():
    src = (ROOT / "ui/src/pages/PolicySim.jsx").read_text()
    assert "Select agent" in src


def test_policy_sim_falls_back_to_text_input():
    src = (ROOT / "ui/src/pages/PolicySim.jsx").read_text()
    # Free-text fallback for when no agents are loaded
    assert "UUID of agent to simulate against" in src


# ── RBAC.jsx: tool dropdown augmented with agent permissions ──────────────────

def test_rbac_tool_dropdown_merges_permissions():
    src = (ROOT / "ui/src/pages/RBAC.jsx").read_text()
    # Dropdown options are the union of TOOL_OPTIONS and existing permission tool names
    assert "new Set([...TOOL_OPTIONS" in src


def test_rbac_tool_dropdown_uses_permission_tool_names():
    src = (ROOT / "ui/src/pages/RBAC.jsx").read_text()
    assert "permissions.map(p => p.tool_name)" in src


# ── SsoSettings.jsx: dynamic providers via getProviders() ────────────────────

def test_sso_settings_has_provider_types_state():
    src = (ROOT / "ui/src/pages/SsoSettings.jsx").read_text()
    assert "providerTypes" in src and "setProviderTypes" in src


def test_sso_settings_calls_get_providers():
    src = (ROOT / "ui/src/pages/SsoSettings.jsx").read_text()
    assert "ssoService.getProviders()" in src


def test_sso_settings_renders_provider_types_from_state():
    src = (ROOT / "ui/src/pages/SsoSettings.jsx").read_text()
    assert "providerTypes.map(pt =>" in src


def test_sso_settings_falls_back_to_hardcoded():
    src = (ROOT / "ui/src/pages/SsoSettings.jsx").read_text()
    # Hardcoded list is still the initial state
    assert "PROVIDER_TYPES" in src and "useState(PROVIDER_TYPES)" in src


# ── Notifications.jsx: accurate unread count via getCount() ──────────────────

def test_notifications_has_total_unread_state():
    src = (ROOT / "ui/src/pages/Notifications.jsx").read_text()
    assert "totalUnread" in src and "setTotalUnread" in src


def test_notifications_calls_get_count():
    src = (ROOT / "ui/src/pages/Notifications.jsx").read_text()
    assert "notificationService.getCount()" in src


def test_notifications_uses_server_count_with_fallback():
    src = (ROOT / "ui/src/pages/Notifications.jsx").read_text()
    assert "totalUnread ?? derivedUnread" in src


def test_notifications_resets_count_on_mark_all():
    src = (ROOT / "ui/src/pages/Notifications.jsx").read_text()
    assert "setTotalUnread(0)" in src
