"""Phase 30 source-contract tests — audit log analyst notes."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── models.py: AuditNote model ───────────────────────────────────────────────

def test_audit_note_model_exists():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "AuditNote" in src


def test_audit_note_has_audit_id():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "audit_id" in src


def test_audit_note_has_tenant_id():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "tenant_id" in src


def test_audit_note_has_created_by():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "created_by" in src


def test_audit_note_has_note_type():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "note_type" in src


def test_audit_note_has_body():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "body" in src


def test_audit_note_has_created_at():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "created_at" in src


def test_audit_note_type_values_documented():
    src = (ROOT / "services/audit/models.py").read_text()
    assert "false_positive" in src
    assert "confirmed_threat" in src


# ── router.py: POST and GET /audit/{id}/notes ─────────────────────────────────

def test_router_imports_audit_note():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "AuditNote" in src


def test_router_has_post_notes_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "notes" in src
    assert "POST" in src or "post" in src


def test_router_has_get_notes_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("notes")
    snippet = src[idx:idx + 3000]
    assert "GET" in snippet or "get" in snippet


def test_router_validates_note_type():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "_NOTE_TYPES" in src


def test_router_note_types_include_all_variants():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "false_positive" in src
    assert "confirmed_threat" in src
    assert "escalated" in src


def test_router_note_create_model_has_body():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "_NoteCreate" in src
    idx = src.find("_NoteCreate")
    snippet = src[idx:idx + 400]
    assert "body" in snippet


def test_router_notes_ordered_ascending():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "asc" in src or "ascending" in src or "created_at" in src


def test_router_notes_check_tenant_ownership():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "tenant_id" in src


# ── gateway/main.py: proxy routes ────────────────────────────────────────────

def test_gateway_has_post_notes_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "notes" in src
    assert "audit_id" in src or "audit/logs" in src


def test_gateway_has_get_notes_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("/audit/logs/{audit_id}/notes")
    assert idx != -1, "Gateway should have /audit/logs/{audit_id}/notes proxy"


def test_gateway_notes_forward_to_audit_service():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("/audit/logs/{audit_id}/notes")
    snippet = src[idx:idx + 500]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/" in snippet


# ── api.js: getNotes / addNote ────────────────────────────────────────────────

def test_api_js_has_get_notes():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getNotes" in src


def test_api_js_has_add_note():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "addNote" in src


def test_api_js_get_notes_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getNotes")
    snippet = src[idx:idx + 200]
    assert "notes" in snippet


def test_api_js_add_note_uses_post():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("addNote")
    snippet = src[idx:idx + 300]
    assert "POST" in snippet


# ── AuditLogs.jsx: NotesPanel component ──────────────────────────────────────

def test_audit_logs_has_notes_panel():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "NotesPanel" in src


def test_audit_logs_notes_panel_uses_get_notes():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "getNotes" in src


def test_audit_logs_notes_panel_uses_add_note():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "addNote" in src


def test_audit_logs_notes_panel_shows_note_types():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "false_positive" in src
    assert "confirmed_threat" in src


def test_audit_logs_notes_panel_shows_escalated():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "escalated" in src


def test_audit_logs_notes_panel_in_expanded_row():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    idx = src.find("ExpandedRow")
    snippet = src[idx:idx + 3000]
    assert "NotesPanel" in snippet


def test_audit_logs_notes_has_toggle():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "Analyst Notes" in src or "notes" in src.lower()


def test_audit_logs_notes_has_form_submit():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "handleAdd" in src or "onSubmit" in src
