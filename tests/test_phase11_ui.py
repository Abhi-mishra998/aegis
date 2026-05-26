"""Phase 11 UI source-contract tests — AutonomyContracts UX hardening + AdminConsole heatmap."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── AutonomyContracts: no more alert() / confirm() ───────────────────────────

def test_autonomy_contracts_no_alert():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "alert(" not in src


def test_autonomy_contracts_no_confirm():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "confirm(" not in src


def test_autonomy_contracts_uses_confirm_dialog():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "ConfirmDialog" in src


def test_autonomy_contracts_imports_confirm_dialog():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "ConfirmDialog" in src
    assert "Common/ConfirmDialog" in src


def test_autonomy_contracts_uses_add_toast():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "addToast" in src


def test_autonomy_contracts_imports_use_auth():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "useAuth" in src


def test_autonomy_contracts_has_disable_target_state():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert "disableTarget" in src
    assert "setDisableTarget" in src


def test_autonomy_contracts_confirm_dialog_is_danger():
    src = (ROOT / "ui/src/pages/AutonomyContracts.jsx").read_text()
    assert 'variant="danger"' in src


# ── AdminConsole: real heatmap, no Math.random ───────────────────────────────

def test_admin_console_no_math_random():
    src = (ROOT / "ui/src/pages/AdminConsole.jsx").read_text()
    assert "Math.random" not in src


def test_admin_console_calls_get_heatmap():
    src = (ROOT / "ui/src/pages/AdminConsole.jsx").read_text()
    assert "getHeatmap" in src


# ── api.js: auditService.getHeatmap ──────────────────────────────────────────

def test_audit_service_has_get_heatmap():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getHeatmap" in src
    assert "/audit/logs/heatmap" in src
