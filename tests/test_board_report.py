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
