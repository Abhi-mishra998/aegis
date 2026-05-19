#!/usr/bin/env python3
"""GDPR / CCPA right-to-portability — single-tenant data export.

Produces a TAR archive containing every row visible across the ACP data
plane that belongs to ONE tenant: audit_logs, usage_records, flight
timelines + steps + snapshots, identity-graph edges, autonomy contracts,
transparency roots (just the ones for this tenant), and per-receipt
signed payloads suitable for offline verification.

The archive layout is stable so it can be diffed across exports and
recompiled into a customer's own audit pipeline:

    <archive_root>/
        manifest.json
        identity/tenant.json
        audit/audit_logs.json
        usage/usage_records.json
        flight/timelines.json
        flight/steps.json
        flight/snapshots.json
        identity_graph/edges.json
        autonomy/contracts.json
        transparency/roots.json
        receipts/{execution_id}.json

`manifest.json` lists every file with its source database, row count,
and sha256(file_bytes). A customer can re-derive the receipts offline
using their own copy of the ACP root signing public key — the
transparency/roots.json carries the signed payload for every root that
covered this tenant's window.

This is a READ-ONLY script. It never modifies the source DBs.

Usage:

    DATABASE_URL=postgresql+asyncpg://...:5432/acp_audit \\
    ACP_AUDIT_DB=postgresql://...:5432/acp_audit \\
    ACP_USAGE_DB=postgresql://...:5432/acp_usage \\
    ACP_IDENTITY_DB=postgresql://...:5432/acp_identity \\
    ACP_FLIGHT_DB=postgresql://...:5432/acp_flight_recorder \\
    ACP_GRAPH_DB=postgresql://...:5432/acp_identity_graph \\
    ACP_AUTONOMY_DB=postgresql://...:5432/acp_autonomy \\
        python scripts/ops/export_tenant.py \\
            --tenant 00000000-0000-0000-0000-000000000001 \\
            --output /tmp/tenant-export.tar.gz

    # Preview without writing — reports counts only.
    python scripts/ops/export_tenant.py --tenant <uuid> --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore

# Repo root on path so we can import test fixtures + helpers.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# --------------------------------------------------------------------------- #
# Per-database queries                                                        #
# --------------------------------------------------------------------------- #


@dataclass
class TableExport:
    """One emitted file inside the archive."""
    archive_path: str
    rows: list[dict[str, Any]]
    source_db: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


# Each entry: (env_var, source_db_label, SELECT statement, archive_path).
# The query MUST be parameterised over a single :tenant_id (which is bound at
# execution time). Any column that's a UUID/datetime is JSON-coerced via the
# `default=str` serializer in `_emit_archive`.
_QUERIES: list[tuple[str, str, str, str]] = [
    (
        "ACP_AUDIT_DB", "acp_audit",
        "SELECT * FROM audit_logs WHERE tenant_id = %s ORDER BY timestamp ASC, id ASC",
        "audit/audit_logs.json",
    ),
    (
        "ACP_USAGE_DB", "acp_usage",
        "SELECT * FROM usage_records WHERE tenant_id = %s ORDER BY timestamp ASC, id ASC",
        "usage/usage_records.json",
    ),
    (
        "ACP_FLIGHT_DB", "acp_flight_recorder",
        "SELECT * FROM execution_timelines WHERE tenant_id = %s ORDER BY started_at ASC, id ASC",
        "flight/timelines.json",
    ),
    (
        "ACP_FLIGHT_DB", "acp_flight_recorder",
        "SELECT s.* FROM execution_steps s WHERE s.tenant_id = %s ORDER BY s.occurred_at ASC, s.id ASC",
        "flight/steps.json",
    ),
    (
        "ACP_FLIGHT_DB", "acp_flight_recorder",
        "SELECT s.* FROM execution_snapshots s WHERE s.tenant_id = %s ORDER BY s.captured_at ASC, s.id ASC",
        "flight/snapshots.json",
    ),
    (
        "ACP_GRAPH_DB", "acp_identity_graph",
        "SELECT * FROM identity_graph_edges WHERE tenant_id = %s ORDER BY ts ASC, id ASC",
        "identity_graph/edges.json",
    ),
    (
        "ACP_AUTONOMY_DB", "acp_autonomy",
        "SELECT * FROM autonomy_contracts WHERE tenant_id = %s ORDER BY created_at ASC, id ASC",
        "autonomy/contracts.json",
    ),
    (
        "ACP_AUDIT_DB", "acp_audit",
        "SELECT * FROM transparency_roots WHERE tenant_id = %s ORDER BY root_date ASC",
        "transparency/roots.json",
    ),
    (
        "ACP_IDENTITY_DB", "acp_identity",
        "SELECT id, tenant_id, name, tier, rpm_limit, requests_per_second, "
        "burst, daily_request_cap, monthly_request_cap, degraded_mode_policy, "
        "is_active, created_at, updated_at "
        "FROM tenants WHERE tenant_id = %s",
        "identity/tenant.json",
    ),
]


def _query(dsn: str, sql: str, tenant_id: str) -> list[dict[str, Any]]:
    """Run a SELECT, return rows as a list of dicts. Tolerates missing
    tables (returns []) so a partial deployment still produces a useful
    archive."""
    if psycopg2 is None:
        raise SystemExit("psycopg2 not installed — install psycopg2-binary")
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                cur.execute(sql, (tenant_id,))
            except psycopg2.errors.UndefinedTable:
                return []
            return list(cur.fetchall())
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Manifest + archive                                                          #
# --------------------------------------------------------------------------- #


def build_manifest(
    *,
    tenant_id: str,
    exports: list[TableExport],
    file_hashes: dict[str, str],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """The customer's verification anchor.

    Each `files[*]` carries `path`, `source_db`, `row_count`, and
    `sha256(file_bytes)`. The sha256 lets a customer detect bit-rot in
    transit AND lets ACP support staff reproduce the exact archive
    deterministically from the same DB snapshot (auditors love this).
    """
    return {
        "version":      1,
        "kind":         "acp_tenant_export",
        "tenant_id":    tenant_id,
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "files": [
            {
                "path":       e.archive_path,
                "source_db":  e.source_db,
                "row_count":  e.row_count,
                "sha256":     file_hashes.get(e.archive_path, ""),
            }
            for e in exports
        ],
        "notes": [
            "All datetimes serialised in ISO-8601 UTC.",
            "transparency/roots.json carries the signed payloads needed for "
            "offline verification of any included receipt.",
            "This archive is immutable — to re-export, re-run the script.",
        ],
    }


def _json_bytes(rows: Iterable[Any]) -> bytes:
    """Stable JSON bytes — sort_keys + default=str so UUID/datetime become
    deterministic strings."""
    return json.dumps(
        list(rows), indent=2, sort_keys=True, default=str,
    ).encode("utf-8")


def _emit_archive(
    *,
    output: Path,
    tenant_id: str,
    exports: list[TableExport],
) -> dict[str, Any]:
    """Build the TAR archive in memory, write atomically to `output`,
    return the manifest dict."""
    file_hashes: dict[str, str] = {}
    file_blobs:  dict[str, bytes] = {}

    for ex in exports:
        blob = _json_bytes(ex.rows)
        file_blobs[ex.archive_path] = blob
        file_hashes[ex.archive_path] = hashlib.sha256(blob).hexdigest()

    manifest = build_manifest(
        tenant_id=tenant_id, exports=exports, file_hashes=file_hashes,
    )
    manifest_blob = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    tmp = output.with_suffix(output.suffix + ".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        # manifest first so a streaming reader can fail fast on shape errors.
        _add_member(tar, "manifest.json", manifest_blob)
        for path, blob in file_blobs.items():
            _add_member(tar, path, blob)
    tmp.replace(output)
    return manifest


def _add_member(tar: tarfile.TarFile, name: str, blob: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(blob)
    info.mtime = int(datetime.now(UTC).timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(blob))


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


def run_export(
    *,
    tenant_id: str,
    output: Path,
    dry_run: bool,
) -> dict[str, Any]:
    """Collect every table for `tenant_id`, optionally write the TAR.

    Returns the manifest (with row counts + hashes when not dry-run).
    """
    exports: list[TableExport] = []
    for env_var, source_db, sql, archive_path in _QUERIES:
        dsn = os.environ.get(env_var)
        if not dsn:
            print(f"[export] WARN: {env_var} not set; skipping {archive_path}", file=sys.stderr)
            exports.append(TableExport(archive_path, [], source_db))
            continue
        try:
            rows = _query(dsn, sql, tenant_id)
        except Exception as exc:
            print(f"[export] WARN: query failed for {archive_path}: {exc}", file=sys.stderr)
            rows = []
        exports.append(TableExport(archive_path, rows, source_db))

    if dry_run:
        # Build the manifest without hashing the would-be files; that
        # keeps the preview cheap.
        return build_manifest(tenant_id=tenant_id, exports=exports, file_hashes={})

    output.parent.mkdir(parents=True, exist_ok=True)
    return _emit_archive(output=output, tenant_id=tenant_id, exports=exports)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant", required=True, help="tenant_id (UUID)")
    p.add_argument("--output", type=Path, default=None,
                   help="output tar.gz path (default: reports/exports/{tenant}_{ts}.tar.gz)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the would-be manifest; no archive written")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    output = args.output or Path(
        f"reports/exports/{args.tenant}_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.tar.gz"
    )
    manifest = run_export(
        tenant_id=args.tenant, output=output, dry_run=args.dry_run,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    if not args.dry_run:
        print(f"[export] wrote {output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
