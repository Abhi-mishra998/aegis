"""Phase 8 UI source-contract tests: PolicySim + UserManagement + Audit Export."""
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI   = ROOT / "ui/src"

def src(rel): return (UI / rel).read_text()

# ── Policy Simulation ────────────────────────────────────────────────────────

def test_policy_sim_page_exists():
    assert (UI / "pages/PolicySim.jsx").exists()

def test_policy_sim_uses_policy_service():
    code = src("pages/PolicySim.jsx")
    assert "policyService" in code
    assert "simulate" in code

def test_policy_sim_has_rules_builder():
    code = src("pages/PolicySim.jsx")
    assert "newRule" in code or "RuleCard" in code

def test_policy_sim_has_time_range():
    code = src("pages/PolicySim.jsx")
    assert "time_range" in code or "timeRange" in code

def test_policy_sim_shows_diff_table():
    code = src("pages/PolicySim.jsx")
    assert "DiffTable" in code or "diff" in code

def test_policy_sim_in_app_routes():
    code = src("App.jsx")
    assert "policy-sim" in code
    assert "PolicySim" in code

# ── User Management ──────────────────────────────────────────────────────────

def test_user_management_page_exists():
    assert (UI / "pages/UserManagement.jsx").exists()

def test_user_management_uses_user_service():
    code = src("pages/UserManagement.jsx")
    assert "userService" in code

def test_user_management_has_invite():
    code = src("pages/UserManagement.jsx")
    assert "invite" in code.lower()

def test_user_management_has_role_change():
    code = src("pages/UserManagement.jsx")
    assert "role" in code
    assert "ROLES" in code or "ADMIN" in code

def test_user_management_in_app_routes():
    code = src("App.jsx")
    assert "/users" in code
    assert "UserManagement" in code

def test_user_management_in_settings():
    code = src("pages/Settings.jsx")
    assert "/users" in code
    assert "User Management" in code

# ── Audit Export ─────────────────────────────────────────────────────────────

def test_audit_logs_has_export_button():
    code = src("pages/AuditLogs.jsx")
    assert "Export" in code or "export" in code
    assert "CSV" in code

def test_audit_logs_imports_export_service():
    code = src("pages/AuditLogs.jsx")
    assert "auditExportService" in code

def test_policy_sim_in_settings():
    code = src("pages/Settings.jsx")
    assert "policy-sim" in code
    assert "Policy Simulation" in code

def test_app_imports_user_management():
    code = src("App.jsx")
    assert "UserManagement" in code
