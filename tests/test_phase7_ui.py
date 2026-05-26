"""Phase 7 UI source-contract tests: Command Palette + Live Feed + wiring."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI   = ROOT / "ui/src"

def src(rel): return (UI / rel).read_text()

# ── Command Palette ──────────────────────────────────────────────────────────

def test_command_palette_exists():
    assert (UI / "components/Common/CommandPalette.jsx").exists()

def test_command_palette_has_live_feed_entry():
    code = src("components/Common/CommandPalette.jsx")
    assert "live-feed" in code

def test_command_palette_has_sso_entry():
    code = src("components/Common/CommandPalette.jsx")
    assert "/sso" in code

def test_command_palette_has_notifications_entry():
    code = src("components/Common/CommandPalette.jsx")
    assert "/notifications" in code

def test_command_palette_has_danger_group():
    code = src("components/Common/CommandPalette.jsx")
    assert "Danger" in code
    assert "kill-switch" in code

# ── App.jsx wiring ───────────────────────────────────────────────────────────

def test_app_imports_command_palette():
    code = src("App.jsx")
    assert "CommandPalette" in code

def test_app_has_palette_open_state():
    code = src("App.jsx")
    assert "paletteOpen" in code

def test_app_wires_mod_k_to_palette():
    code = src("App.jsx")
    assert "onShowPalette" in code
    assert "mod+k" in code

def test_app_renders_command_palette():
    code = src("App.jsx")
    assert "<CommandPalette" in code

def test_app_has_live_feed_route():
    code = src("App.jsx")
    assert "live-feed" in code
    assert "LiveFeed" in code

# ── Live Feed page ───────────────────────────────────────────────────────────

def test_live_feed_page_exists():
    assert (UI / "pages/LiveFeed.jsx").exists()

def test_live_feed_uses_sse_hook():
    code = src("pages/LiveFeed.jsx")
    assert "useSSE" in code

def test_live_feed_has_pause_resume():
    code = src("pages/LiveFeed.jsx")
    assert "paused" in code
    assert "Pause" in code or "pause" in code

def test_live_feed_has_event_type_filters():
    code = src("pages/LiveFeed.jsx")
    assert "filterTypes" in code

def test_live_feed_has_investigate_action():
    code = src("pages/LiveFeed.jsx")
    assert "investigate" in code.lower() or "forensics" in code.lower()

def test_sidebar_has_live_feed():
    code = src("components/Layout/Sidebar.jsx")
    assert "live-feed" in code
    assert "Live Feed" in code
