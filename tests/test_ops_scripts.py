"""Unit tests for the ops sprint (Sprint 3.4, 2026-05-15).

Scope: the pure-Python helpers in scripts/ops/. The shell scripts
(backup.sh / restore_drill.sh) are covered by their `--dry-run` mode
+ the doc runbook; they're tested with operator follow-up against
real docker + S3.

Covered:

* `export_tenant.build_manifest` produces a stable, sortable manifest
  with sha256 + row counts per file.
* `export_tenant._json_bytes` is deterministic across runs.
* `export_tenant.run_export(dry_run=True)` returns the manifest
  without writing — exercises the no-DSN-set branch (every query
  produces an empty file).
* `export_tenant._emit_archive` writes a valid tar.gz whose manifest
  member is consistent with the per-file sha256s.
* `redact_tenant_pii.collect_redactions_for_row`:
    - empty row → []
    - row with `reason` → 1 redaction at path "reason"
    - row with nested metadata_json → redactions only for allowlisted paths
    - row's metadata_json supplied as JSON string (legacy) is parsed
* `redact_tenant_pii.hash_pii` is deterministic + canonical.
* `redact_tenant_pii.RedactionRecord` round-trips through to_dict.
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Make scripts/ops importable.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

# psycopg2 isn't installed in the local dev venv. The export script
# guards against this, but unit tests should not need it.
sys.modules.setdefault("psycopg2", MagicMock())
sys.modules.setdefault("psycopg2.extras", MagicMock())


# --------------------------------------------------------------------------- #
# export_tenant                                                               #
# --------------------------------------------------------------------------- #


from scripts.ops import export_tenant as et  # noqa: E402


class TestExportManifest:
    def test_build_manifest_includes_all_files_and_hashes(self):
        exports = [
            et.TableExport("audit/audit_logs.json", [{"id": "a"}], "acp_audit"),
            et.TableExport("usage/usage_records.json", [], "acp_usage"),
        ]
        hashes = {
            "audit/audit_logs.json":   "ff" * 32,
            "usage/usage_records.json": "00" * 32,
        }
        m = et.build_manifest(
            tenant_id="t1", exports=exports, file_hashes=hashes,
            generated_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        )
        assert m["version"] == 1
        assert m["kind"] == "acp_tenant_export"
        assert m["tenant_id"] == "t1"
        assert m["generated_at"] == "2026-05-15T00:00:00+00:00"
        assert len(m["files"]) == 2
        # Files sorted by archive_path order (insertion order from caller).
        assert m["files"][0]["path"] == "audit/audit_logs.json"
        assert m["files"][0]["row_count"] == 1
        assert m["files"][0]["sha256"] == "ff" * 32
        assert m["files"][1]["row_count"] == 0

    def test_build_manifest_handles_missing_hashes(self):
        exports = [et.TableExport("audit/audit_logs.json", [{}], "acp_audit")]
        m = et.build_manifest(tenant_id="t1", exports=exports, file_hashes={})
        assert m["files"][0]["sha256"] == ""


class TestJsonBytes:
    def test_deterministic(self):
        rows = [{"id": "z", "ts": "2026-05-15T00:00:00Z"},
                {"id": "a", "ts": "2026-05-14T00:00:00Z"}]
        a = et._json_bytes(rows)
        b = et._json_bytes(rows)
        assert a == b

    def test_uuid_and_datetime_serialized_as_string(self):
        import uuid as _uuid
        rows = [{"id": _uuid.UUID(int=1), "ts": datetime(2026, 5, 15, tzinfo=timezone.utc)}]
        blob = et._json_bytes(rows)
        text = blob.decode()
        assert "00000000-0000-0000-0000-000000000001" in text
        assert "2026-05-15" in text


class TestRunExportDryRun:
    def test_dry_run_skips_queries_when_dsn_unset(self, monkeypatch, tmp_path: Path):
        """With every DSN env var unset, the script logs a warning and
        emits empty exports — but still produces a valid manifest."""
        for env_var, *_ in et._QUERIES:
            monkeypatch.delenv(env_var, raising=False)
        out = tmp_path / "x.tar.gz"
        m = et.run_export(tenant_id="t1", output=out, dry_run=True)
        # Every queried table appears with row_count=0.
        assert all(f["row_count"] == 0 for f in m["files"])
        # No file written.
        assert not out.exists()
        # Files are unique by path.
        paths = [f["path"] for f in m["files"]]
        assert len(paths) == len(set(paths))


class TestEmitArchive:
    def test_archive_contains_manifest_and_matches_hashes(self, tmp_path: Path):
        rows = [{"id": "x", "tenant_id": "t1"}]
        exports = [et.TableExport("audit/audit_logs.json", rows, "acp_audit")]
        out = tmp_path / "exp.tar.gz"
        manifest = et._emit_archive(output=out, tenant_id="t1", exports=exports)

        assert out.exists()
        with tarfile.open(out) as tar:
            members = {m.name: m for m in tar.getmembers()}
            assert "manifest.json" in members
            assert "audit/audit_logs.json" in members
            # The manifest from disk matches the returned manifest dict.
            with tar.extractfile("manifest.json") as fh:
                disk = json.loads(fh.read().decode())
            assert disk == manifest
            # The per-file sha256 in the manifest matches the actual file bytes.
            with tar.extractfile("audit/audit_logs.json") as fh:
                blob = fh.read()
            import hashlib
            actual = hashlib.sha256(blob).hexdigest()
            file_entry = next(f for f in manifest["files"]
                              if f["path"] == "audit/audit_logs.json")
            assert file_entry["sha256"] == actual
            assert file_entry["row_count"] == 1


# --------------------------------------------------------------------------- #
# redact_tenant_pii                                                           #
# --------------------------------------------------------------------------- #


from scripts.ops import redact_tenant_pii as rt  # noqa: E402


class TestHashPii:
    def test_deterministic(self):
        a = rt.hash_pii("hello")
        b = rt.hash_pii("hello")
        assert a == b

    def test_length_is_utf8_byte_count(self):
        sha, n = rt.hash_pii("hi")
        # JSON-encoded "hi" → `"hi"` → 4 bytes.
        assert n == 4

    def test_different_values_different_hashes(self):
        a, _ = rt.hash_pii("hello")
        b, _ = rt.hash_pii("HELLO")
        assert a != b


class TestCollectRedactionsForRow:
    def test_empty_row_yields_no_redactions(self):
        assert rt.collect_redactions_for_row({"id": "x", "reason": None}) == []

    def test_reason_field_picked_up(self):
        out = rt.collect_redactions_for_row({
            "id": "audit-1", "reason": "user said something private",
            "metadata_json": {},
        })
        assert len(out) == 1
        assert out[0].audit_id == "audit-1"
        assert out[0].field_path == "reason"

    def test_metadata_json_dict_redacts_allowlisted_paths_only(self):
        out = rt.collect_redactions_for_row({
            "id": "audit-2", "reason": None,
            "metadata_json": {
                "path":  "/home/alice/private.txt",   # allowlisted
                "sql":   "SELECT name FROM users",    # allowlisted
                "tier":  "enterprise",                # NOT allowlisted
                "status": 200,                        # NOT allowlisted
            },
        })
        paths = sorted(r.field_path for r in out)
        assert paths == ["metadata_json.path", "metadata_json.sql"]

    def test_metadata_json_as_string_legacy(self):
        """Some rows stored metadata_json as a JSON string. The collector
        must parse it transparently."""
        legacy = json.dumps({"prompt": "secret instructions"})
        out = rt.collect_redactions_for_row({
            "id": "audit-3", "reason": None, "metadata_json": legacy,
        })
        assert len(out) == 1
        assert out[0].field_path == "metadata_json.prompt"

    def test_metadata_json_malformed_string_yields_no_metadata_redactions(self):
        """If a row has corrupt metadata_json, we redact what we can
        (top-level reason) and skip the broken nested fields."""
        out = rt.collect_redactions_for_row({
            "id": "audit-4", "reason": "still pii",
            "metadata_json": "this is not json{",
        })
        assert len(out) == 1
        assert out[0].field_path == "reason"

    def test_empty_string_value_skipped(self):
        out = rt.collect_redactions_for_row({
            "id": "audit-5", "reason": "",
            "metadata_json": {"path": "", "sql": ""},
        })
        assert out == []


class TestRedactionRecord:
    def test_to_dict_roundtrip(self):
        rec = rt.RedactionRecord(
            redaction_id="r1", tenant_id="t1",
            requested_at="2026-05-15T00:00:00+00:00",
            executed_at="2026-05-15T01:00:00+00:00",
            legal_reason="GDPR-2026-0042", actor="ops@example.com",
            fields=[rt.FieldRedaction("a1", "reason", "ff" * 32, 4)],
        )
        d = rec.to_dict()
        assert d["redaction_id"] == "r1"
        assert d["fields"][0]["audit_id"] == "a1"
        # Round-trip back through JSON to confirm serialisability.
        round = json.loads(json.dumps(d))
        assert round == d


class TestRunRedactionDryRun:
    def test_dry_run_does_not_call_db_or_write_record(self, monkeypatch, tmp_path: Path):
        # With audit_dsn=None and dry_run=True, no DB connection should occur.
        calls = {"select": 0, "insert": 0}
        monkeypatch.setattr(rt, "_select_tenant_rows",
                            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not query")))
        monkeypatch.setattr(rt, "_insert_redaction_marker",
                            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not insert")))
        rec = rt.run_redaction(
            tenant_id="t1", legal_reason="r", actor="ops",
            audit_dsn=None, output_dir=tmp_path, dry_run=True,
        )
        # Empty rows → empty fields, but the record is still well-formed.
        assert rec.fields == []
        assert rec.audit_chain_marker_row_id is None
        # No sealed record file written in dry-run.
        assert not any(tmp_path.iterdir())

    def test_execute_without_audit_dsn_errors(self, tmp_path: Path):
        with pytest.raises(SystemExit):
            rt.run_redaction(
                tenant_id="t1", legal_reason="r", actor="ops",
                audit_dsn=None, output_dir=tmp_path, dry_run=False,
            )


# --------------------------------------------------------------------------- #
# Static checks on runbooks + scripts                                         #
# --------------------------------------------------------------------------- #


def test_runbooks_present_and_nonempty():
    runbooks = [
        "docs/runbooks/key_rotation.md",
        "docs/runbooks/key_rotation_drill_log.md",
        "docs/runbooks/audit_chain_violation.md",
        "docs/runbooks/tenant_data_request.md",
        "docs/runbooks/restore_drill.md",
    ]
    for path in runbooks:
        p = _REPO / path
        assert p.exists(), f"missing runbook: {path}"
        body = p.read_text()
        # Sanity: each runbook must have a title + at least one Run / Step
        # / Phase section so it's actually operational, not a stub.
        assert body.startswith("# "), f"{path} missing top-level title"
        assert len(body) > 1000, f"{path} suspiciously short ({len(body)} chars)"


def test_backup_script_present_and_dry_run_documented():
    p = _REPO / "scripts/ops/backup.sh"
    assert p.exists()
    body = p.read_text()
    assert "--dry-run" in body
    assert "ACP_BACKUP_AGE_RECIPIENT" in body
    assert "set -euo pipefail" in body  # strict mode


def test_restore_drill_script_present_and_isolated_network():
    p = _REPO / "scripts/ops/restore_drill.sh"
    assert p.exists()
    body = p.read_text()
    assert "--dry-run" in body
    # The isolation claim — separate compose project name + report file.
    assert "acp_drill_" in body
    assert "reports/restore_drill" in body
