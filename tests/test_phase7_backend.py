"""
Phase 7 backend source-contract tests.

These tests verify file-level contracts only — no imports, no running server.
They check that all required models, endpoints, migrations, and API client
methods exist in the expected source files.
"""
from __future__ import annotations

from pathlib import Path

AUDIT_MODELS    = Path(__file__).parent.parent / "services/audit/models.py"
COMPLIANCE      = Path(__file__).parent.parent / "services/audit/compliance.py"
MIGRATION       = Path(__file__).parent.parent / "services/audit/alembic/versions/t3u4v5w6x7y8_incident_comments.py"
GATEWAY         = Path(__file__).parent.parent / "services/gateway/main.py"
API_JS          = Path(__file__).parent.parent / "ui/src/services/api.js"


# ── helpers ────────────────────────────────────────────────────────────────

def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── 1. IncidentComment model exists in models.py ───────────────────────────

def test_incident_comment_model_in_models():
    src = _read(AUDIT_MODELS)
    assert "class IncidentComment" in src, \
        "IncidentComment class not found in services/audit/models.py"


# ── 2. PATCH /incidents/{id} endpoint in compliance.py ────────────────────

def test_patch_incident_endpoint_in_compliance():
    src = _read(COMPLIANCE)
    assert 'incidents_router.patch("/{incident_id}"' in src or \
           "@incidents_router.patch" in src, \
        "PATCH /incidents/{incident_id} endpoint not found in compliance.py"


# ── 3. POST /incidents/{id}/comments endpoint in compliance.py ─────────────

def test_post_comments_endpoint_in_compliance():
    src = _read(COMPLIANCE)
    assert '/{incident_id}/comments"' in src, \
        "POST /incidents/{incident_id}/comments endpoint not found in compliance.py"


# ── 4. GET /incidents/{id}/comments endpoint in compliance.py ──────────────

def test_get_comments_endpoint_in_compliance():
    src = _read(COMPLIANCE)
    assert "list_comments" in src, \
        "GET /incidents/{incident_id}/comments (list_comments) not found in compliance.py"


# ── 5. Alembic migration file exists ──────────────────────────────────────

def test_migration_file_exists():
    assert MIGRATION.exists(), \
        f"Migration file not found: {MIGRATION}"


# ── 6. Gateway has PATCH /incidents proxy ─────────────────────────────────

def test_gateway_patch_incidents_proxy():
    src = _read(GATEWAY)
    assert '@app.patch("/incidents/{incident_id}"' in src, \
        "PATCH /incidents/{incident_id} proxy not found in gateway/main.py"


# ── 7. Gateway has POST /incidents/{id}/comments proxy ────────────────────

def test_gateway_post_comments_proxy():
    src = _read(GATEWAY)
    assert '@app.post("/incidents/{incident_id}/comments"' in src, \
        "POST /incidents/{incident_id}/comments proxy not found in gateway/main.py"


# ── 8. Gateway has GET /incidents/{id}/comments proxy ────────────────────

def test_gateway_get_comments_proxy():
    src = _read(GATEWAY)
    assert '@app.get("/incidents/{incident_id}/comments"' in src, \
        "GET /incidents/{incident_id}/comments proxy not found in gateway/main.py"


# ── 9. api.js incidentService has update method ───────────────────────────

def test_api_js_incident_service_update():
    src = _read(API_JS)
    assert "update:" in src or "update :" in src, \
        "incidentService.update method not found in ui/src/services/api.js"


# ── 10. api.js incidentService has getComments method ────────────────────

def test_api_js_incident_service_get_comments():
    src = _read(API_JS)
    assert "getComments" in src, \
        "incidentService.getComments method not found in ui/src/services/api.js"


# ── 11. api.js incidentService has addComment method ─────────────────────

def test_api_js_incident_service_add_comment():
    src = _read(API_JS)
    assert "addComment" in src, \
        "incidentService.addComment method not found in ui/src/services/api.js"


# ── 12. IncidentComment has incident_id and body columns ──────────────────

def test_incident_comment_has_required_columns():
    src = _read(AUDIT_MODELS)
    assert "incident_id" in src, \
        "IncidentComment.incident_id column not found in models.py"
    assert "body" in src and "IncidentComment" in src, \
        "IncidentComment.body column not found in models.py"
