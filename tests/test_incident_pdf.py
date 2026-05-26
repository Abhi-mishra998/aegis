"""
Incident Forensic PDF — Source Contract Tests
==============================================
These are static contract tests: no running server, no database, no reportlab
invocation required.  They verify that the code artefacts exist and conform to
the expected interface.

Run:
    python3 -m pytest tests/test_incident_pdf.py -v
"""

from __future__ import annotations

import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Path constants — resolve from project root
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
_INCIDENT_PDF = _ROOT / "services" / "audit" / "incident_pdf.py"
_COMPLIANCE   = _ROOT / "services" / "audit" / "compliance.py"
_GATEWAY      = _ROOT / "services" / "gateway" / "main.py"
_API_JS       = _ROOT / "ui" / "src" / "services" / "api.js"


# ---------------------------------------------------------------------------
# Helper: read source text
# ---------------------------------------------------------------------------

def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1 — incident_pdf.py exists and defines generate_incident_pdf
# ---------------------------------------------------------------------------

def test_incident_pdf_module_has_generate_function():
    """services/audit/incident_pdf.py must exist and contain generate_incident_pdf."""
    assert _INCIDENT_PDF.exists(), (
        f"Expected file not found: {_INCIDENT_PDF}\n"
        "Did you create services/audit/incident_pdf.py?"
    )
    src = _src(_INCIDENT_PDF)
    assert "generate_incident_pdf" in src, (
        "incident_pdf.py does not define or reference 'generate_incident_pdf'."
    )


# ---------------------------------------------------------------------------
# Test 2 — function signature has the three required parameters
# ---------------------------------------------------------------------------

def test_incident_pdf_takes_three_args():
    """generate_incident_pdf must accept incident_data, audit_entries, receipt."""
    src = _src(_INCIDENT_PDF)

    # Use AST to find the function definition without importing (avoids
    # reportlab / DB dependency at test time).
    tree = ast.parse(src)
    func_def = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "generate_incident_pdf":
            func_def = node
            break

    assert func_def is not None, (
        "Could not find a function named 'generate_incident_pdf' in incident_pdf.py."
    )

    arg_names = [arg.arg for arg in func_def.args.args]
    for required in ("incident_data", "audit_entries", "receipt"):
        assert required in arg_names, (
            f"generate_incident_pdf is missing parameter '{required}'. "
            f"Found: {arg_names}"
        )


# ---------------------------------------------------------------------------
# Test 3 — compliance.py contains the incident export route
# ---------------------------------------------------------------------------

def test_incident_pdf_export_route_exists():
    """compliance.py must define a route /incidents/{incident_id}/export."""
    src = _src(_COMPLIANCE)
    # Check for the route decorator pattern
    assert "/incidents/{incident_id}/export" in src, (
        "compliance.py does not contain '/incidents/{incident_id}/export'.\n"
        "Add a POST route for incident PDF export to the compliance_router."
    )
    # Also verify the handler function is defined
    assert "export_incident_pdf" in src, (
        "compliance.py is missing the 'export_incident_pdf' handler function."
    )


# ---------------------------------------------------------------------------
# Test 4 — gateway/main.py proxies the incident export endpoint
# ---------------------------------------------------------------------------

def test_gateway_proxies_incident_export():
    """gateway/main.py must define a proxy route for /incidents/{incident_id}/export."""
    src = _src(_GATEWAY)
    # The route path pattern must appear in the gateway
    assert "incident_id}/export" in src, (
        "gateway/main.py does not proxy '/incidents/{incident_id}/export'.\n"
        "Add a POST route that streams the PDF from the audit service."
    )
    # Verify it proxies to the audit service (compliance prefix)
    assert "/compliance/incidents/" in src, (
        "gateway/main.py proxy for incident export must forward to "
        "'/compliance/incidents/{incident_id}/export' on the audit service."
    )


# ---------------------------------------------------------------------------
# Test 5 — api.js incidentService has exportPdf
# ---------------------------------------------------------------------------

def test_api_js_incident_service_has_export():
    """ui/src/services/api.js incidentService must contain an exportPdf method."""
    assert _API_JS.exists(), f"api.js not found at {_API_JS}"
    src = _src(_API_JS)

    # Verify incidentService block exists
    assert "incidentService" in src, (
        "api.js does not export 'incidentService'."
    )

    # Find the incidentService block and check exportPdf is inside it
    # We look for exportPdf within a reasonable window after incidentService
    idx = src.find("export const incidentService")
    assert idx != -1, "Could not find 'export const incidentService' in api.js."

    # Grab the text from the incidentService definition onward
    after = src[idx:]

    # Find the closing brace of the incidentService object
    # Count braces: we need to find the matching }; after the opening {
    brace_depth = 0
    service_src = ""
    in_block = False
    for char in after:
        if char == "{":
            brace_depth += 1
            in_block = True
        elif char == "}":
            brace_depth -= 1
        service_src += char
        if in_block and brace_depth == 0:
            break

    assert "exportPdf" in service_src, (
        "incidentService in api.js is missing the 'exportPdf' method.\n"
        "Add: exportPdf: async (id) => { ... } to the incidentService object."
    )
    # Also verify it calls the correct endpoint
    assert "/export" in service_src, (
        "incidentService.exportPdf in api.js must call the /incidents/{id}/export endpoint."
    )
