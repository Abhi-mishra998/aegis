"""Source-contract tests for the board-level executive PDF report (no imports needed)."""
import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
_BOARD_REPORT = ROOT / "services/audit/board_report.py"
_AUDIT_MAIN   = ROOT / "services/audit/main.py"
_GATEWAY      = ROOT / "services/gateway/main.py"
_API_JS       = ROOT / "ui/src/services/api.js"
_EXEC_DASH    = ROOT / "ui/src/pages/ExecutiveDashboard.jsx"


def test_board_report_module_exists():
    assert _BOARD_REPORT.exists()


def test_generate_board_report_pdf_function_defined():
    src = _BOARD_REPORT.read_text()
    assert "generate_board_report_pdf" in src


def test_board_report_function_signature():
    tree = ast.parse(_BOARD_REPORT.read_text())
    func = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "generate_board_report_pdf"),
        None,
    )
    assert func is not None, "generate_board_report_pdf not found"
    args = [a.arg for a in func.args.args]
    for param in ("tenant_id", "start_date", "end_date", "summary"):
        assert param in args, f"Missing param: {param}"


def test_board_report_returns_bytes_annotation():
    src = _BOARD_REPORT.read_text()
    assert "bytes" in src


def test_audit_main_has_board_report_endpoint():
    src = _AUDIT_MAIN.read_text()
    assert "/board-report" in src
    assert "generate_board_report_pdf" in src


def test_gateway_proxies_board_report():
    # /compliance/board-report was extracted from main.py to
    # routers/compliance.py in sprint-5; scan both.
    src = (
        _GATEWAY.read_text()
        + (_GATEWAY.parent / "routers" / "compliance.py").read_text()
    )
    assert "board-report" in src
    assert "board_report" in src


def test_api_js_has_board_report():
    src = _API_JS.read_text()
    assert "boardReport" in src
    assert "board-report" in src


def test_executive_dashboard_uses_board_report():
    src = _EXEC_DASH.read_text()
    assert "boardReport" in src or "Board Report" in src


# ---------------------------------------------------------------------------
# SEC: cross-tenant data-leak guard
#
# /board-report previously honored ``payload["tenant_id"]`` and let the body
# value OVERRIDE the JWT-derived header tenant_id. That allowed a tenant-A
# user with a valid JWT to read tenant-B's audit summary by setting
# {"tenant_id": "<tenant-B-uuid>"} in the body.
#
# The fix: ALWAYS use ``tenant_id_dep`` (the dependency-injected, JWT-derived
# header value). Body tenant_id MUST be ignored. These tests assert that
# invariant at the source level so a regression cannot land silently.
# ---------------------------------------------------------------------------

def test_board_report_does_not_honor_body_tenant_id():
    """The handler must never read tenant_id from request body.

    Specifically: the source MUST NOT contain ``payload.get("tenant_id")``
    or any equivalent assignment that would let a request body override the
    dependency-injected ``tenant_id_dep``.
    """
    src = _AUDIT_MAIN.read_text()
    # Direct dict-get on tenant_id from payload is the exact attack site.
    assert 'payload.get("tenant_id")' not in src, (
        "Cross-tenant leak regression: /board-report handler is reading "
        "tenant_id from request body. The tenant scope MUST be taken from "
        "the JWT-derived header (tenant_id_dep), never from the body."
    )
    assert "payload.get('tenant_id')" not in src, (
        "Cross-tenant leak regression (single-quoted variant): "
        "/board-report handler is reading tenant_id from request body."
    )


def test_board_report_handler_uses_only_dependency_tenant_id():
    """AST-level check: the only `tenant_id = …` assignment in the
    board_report handler must be `tenant_id = tenant_id_dep`.

    This catches subtler regressions where someone re-introduces a body /
    body-fallback override via a different attribute path (e.g.
    ``data.get("tenant_id")``).
    """
    tree = ast.parse(_AUDIT_MAIN.read_text())
    handler = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "board_report":
            handler = node
            break
    assert handler is not None, "board_report handler not found"

    # Walk the function body and collect every top-level `tenant_id = X` assignment.
    tenant_id_assignments: list[str] = []
    for sub in ast.walk(handler):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                if isinstance(target, ast.Name) and target.id == "tenant_id":
                    # Record the source of the rhs.
                    tenant_id_assignments.append(ast.unparse(sub.value))

    assert tenant_id_assignments, (
        "board_report handler has no `tenant_id = …` assignment — that's "
        "unexpected; the fix should still set tenant_id from tenant_id_dep."
    )
    # Every assignment must be exactly `tenant_id_dep`. No body / payload fallback.
    for rhs in tenant_id_assignments:
        assert rhs == "tenant_id_dep", (
            f"board_report handler assigns tenant_id from `{rhs}` — only "
            f"`tenant_id_dep` is allowed. Body/payload-derived tenant_id is "
            f"a cross-tenant data leak."
        )


def test_board_report_handler_keeps_jwt_tenant_dependency():
    """The handler must still declare `tenant_id_dep` via Depends(get_tenant_id).

    If a future refactor removes the dependency, the handler would silently
    fall back to no-auth — guard against that here.
    """
    src = _AUDIT_MAIN.read_text()
    # Find the board_report handler signature region.
    assert "tenant_id_dep: Annotated[uuid.UUID, Depends(get_tenant_id)]" in src, (
        "board_report handler must keep its JWT-derived tenant_id_dep "
        "dependency — otherwise tenant scoping is silently lost."
    )
