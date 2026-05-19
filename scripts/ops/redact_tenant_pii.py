#!/usr/bin/env python3
"""GDPR / CCPA right-to-erasure — audit-safe redaction.

THE PROBLEM
-----------
ACP's audit_logs table is the foundation of the transparency chain
(Sprint 1.3). Every row's `event_hash` is computed from its content
and chained to the previous row's `prev_hash`. Mutating the row
content invalidates the chain — `/audit/logs/verify` fails, all
downstream Merkle roots become un-verifiable, and the platform's
core security claim collapses.

But GDPR Article 17 / CCPA §1798.105 still applies. We owe customers
a mechanism to erase their PII without compromising the audit chain.

THE PATTERN
-----------
ACP's recommended architecture (long-term) is *envelope encryption*:

  1. PII fields (request bodies, prompts, response bodies, free-form
     `reason` strings) are encrypted at ingest with a tenant-scoped
     data-encryption key (DEK).
  2. The DEK is wrapped by a per-tenant key-encryption key (KEK)
     held in KMS / HSM.
  3. audit_logs.metadata_json stores ONLY the encrypted blob + the
     KEK identifier, never plaintext PII.
  4. Right-to-erasure = DELETE the tenant's KEK in KMS. The
     ciphertext stays in audit_logs (so event_hash stays valid,
     chain unbroken), but the plaintext is now unrecoverable.

That's the right destination. Where we are TODAY:

  - audit_logs.metadata_json stores plaintext.
  - Receipts (Sprint 1.3) commit to the row contents via Merkle
     leaves. Mutating row contents invalidates the receipts.

WHAT THIS SCRIPT DOES (the interim contract)
--------------------------------------------
It does NOT mutate any existing audit_logs row. Instead it:

  1. SELECTs the rows for the tenant that contain PII (free-form
     `reason` strings, request_id-keyed metadata bodies).
  2. Computes sha256 of each PII value.
  3. INSERTs a new audit_log row with action="pii_redaction" carrying
     the sha256 hashes + a reference to which historical rows they
     correspond to (by id). The new row is chained normally — its
     own event_hash is valid, prev_hash points at the latest chain
     tip — so the audit chain stays integrous.
  4. Writes a sealed redaction record (JSON file) describing exactly
     which fields were redacted; operator stores this alongside the
     legal request for the seven-year retention window.

Once envelope encryption ships (tracked separately), the redaction
flow becomes a single KMS DeleteKey call + a redaction-event row
written by this same script. The script's interface stays stable
across that migration.

The script does NOT delete or rewrite any historical row. Run it
side-by-side with the existing audit chain and operators can audit
exactly what was redacted, when, and by whom.

Usage:

    python scripts/ops/redact_tenant_pii.py \\
        --tenant 00000000-0000-0000-0000-000000000001 \\
        --reason "GDPR-2026-0042" \\
        --execute

    # Preview (default).
    python scripts/ops/redact_tenant_pii.py --tenant <uuid> --reason X
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore


_REPO = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# Redaction record                                                            #
# --------------------------------------------------------------------------- #


@dataclass
class FieldRedaction:
    audit_id:        str
    field_path:      str         # JSONPath inside metadata_json
    original_sha256: str
    length_bytes:    int


@dataclass
class RedactionRecord:
    """Append-only, sealed record. Stored under reports/redactions/{uuid}.json.

    Operators MUST preserve this file for the legal retention window. Lost
    redaction records mean we can't prove we honoured the request.
    """
    redaction_id:   str
    tenant_id:      str
    requested_at:   str           # ISO-8601 — the customer's request timestamp
    executed_at:    str           # when this script ran
    legal_reason:   str
    actor:          str
    fields:         list[FieldRedaction] = field(default_factory=list)
    audit_chain_marker_row_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fields"] = [asdict(f) for f in self.fields]
        return d


# --------------------------------------------------------------------------- #
# PII detection                                                               #
# --------------------------------------------------------------------------- #


def hash_pii(value: Any) -> tuple[str, int]:
    """Return (sha256_hex, length_bytes) of a value's canonical UTF-8 JSON
    encoding. We hash the JSON (not the repr) so the same Python value
    always produces the same digest, regardless of Python build."""
    blob = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest(), len(blob)


# Conservative PII field allowlist — these are the keys we KNOW contain
# user-supplied prose. Adding to this list is the safe direction; removing
# entries should require a legal review.
_PII_FIELD_PATHS: tuple[str, ...] = (
    "reason",         # free-form policy / decision reason
    "metadata_json.path",       # file path arguments — may contain user paths
    "metadata_json.sql",        # SQL — may contain user-identifiers in literals
    "metadata_json.payload",    # entire request payload echo
    "metadata_json.prompt",     # user prompt text
    "metadata_json.response",   # tool response text
    "metadata_json.actor",      # legal-name actor field
)


def _walk(obj: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into JSONPath → value pairs."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_walk(v, f"{prefix}.{k}" if prefix else k))
    else:
        if prefix:
            out[prefix] = obj
    return out


def collect_redactions_for_row(row: dict[str, Any]) -> list[FieldRedaction]:
    """Inspect one audit_logs row, return per-field redaction records for
    every PII-bearing field present.

    Pure function — does NOT touch the DB. Unit-tested directly."""
    audit_id = str(row.get("id"))
    fields: list[FieldRedaction] = []
    # Top-level `reason` column on audit_logs.
    if row.get("reason"):
        sha, n = hash_pii(row["reason"])
        fields.append(FieldRedaction(audit_id, "reason", sha, n))
    # Nested metadata_json fields.
    meta = row.get("metadata_json")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    if isinstance(meta, dict):
        flat = _walk(meta, prefix="metadata_json")
        for path, v in flat.items():
            if path in _PII_FIELD_PATHS and v not in (None, ""):
                sha, n = hash_pii(v)
                fields.append(FieldRedaction(audit_id, path, sha, n))
    return fields


# --------------------------------------------------------------------------- #
# DB access                                                                   #
# --------------------------------------------------------------------------- #


def _select_tenant_rows(audit_dsn: str, tenant_id: str) -> list[dict[str, Any]]:
    if psycopg2 is None:
        raise SystemExit("psycopg2 required (pip install psycopg2-binary)")
    conn = psycopg2.connect(audit_dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, reason, metadata_json, timestamp "
                "FROM audit_logs WHERE tenant_id = %s ORDER BY timestamp ASC",
                (tenant_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _insert_redaction_marker(
    audit_dsn: str,
    *,
    tenant_id: str,
    record: RedactionRecord,
) -> str:
    """Write the redaction event into audit_logs. The row is chained
    normally by the existing audit writer's invariants. Returns the new
    row's id."""
    if psycopg2 is None:
        raise SystemExit("psycopg2 required")
    conn = psycopg2.connect(audit_dsn)
    try:
        with conn.cursor() as cur:
            row_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO audit_logs
                    (id, tenant_id, org_id, agent_id, action, tool, decision,
                     reason, metadata_json, request_id, created_at, updated_at, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), NOW(), NOW())
                """,
                (
                    row_id, tenant_id, tenant_id, tenant_id,
                    "pii_redaction", "redact_tenant_pii.py", "deny",
                    f"redaction:{record.legal_reason}",
                    json.dumps({
                        "redaction_id":   record.redaction_id,
                        "field_count":    len(record.fields),
                        "redacted_audit_ids": sorted({f.audit_id for f in record.fields})[:50],
                        "actor":          record.actor,
                        "requested_at":   record.requested_at,
                    }),
                    record.redaction_id,
                ),
            )
            conn.commit()
            return row_id
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #


def run_redaction(
    *,
    tenant_id: str,
    legal_reason: str,
    actor: str,
    audit_dsn: str | None,
    output_dir: Path,
    dry_run: bool,
    requested_at: datetime | None = None,
) -> RedactionRecord:
    record = RedactionRecord(
        redaction_id=str(uuid.uuid4()),
        tenant_id=str(tenant_id),
        requested_at=(requested_at or datetime.now(UTC)).isoformat(),
        executed_at=datetime.now(UTC).isoformat(),
        legal_reason=legal_reason,
        actor=actor,
    )

    if not audit_dsn:
        # Permitted in dry-run; otherwise refuse so the operator isn't
        # surprised by a zero-row "success".
        if not dry_run:
            raise SystemExit("ACP_AUDIT_DB required for --execute")
        rows: list[dict[str, Any]] = []
    else:
        rows = _select_tenant_rows(audit_dsn, tenant_id)

    for row in rows:
        record.fields.extend(collect_redactions_for_row(row))

    if not dry_run:
        record.audit_chain_marker_row_id = _insert_redaction_marker(
            audit_dsn or "", tenant_id=tenant_id, record=record,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{record.redaction_id}.json"
    if not dry_run:
        out_path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True))

    return record


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant", required=True, help="tenant_id (UUID)")
    p.add_argument("--reason", required=True,
                   help="legal request reference (e.g. GDPR-2026-0042)")
    p.add_argument("--actor", default=os.environ.get("USER", "ops"),
                   help="operator name/email recorded with the redaction")
    p.add_argument("--audit-db", default=os.environ.get("ACP_AUDIT_DB"),
                   help="psycopg2 DSN for acp_audit")
    p.add_argument("--output-dir", type=Path,
                   default=_REPO / "reports" / "redactions")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="(default) preview only, no DB writes, no record file")
    mode.add_argument("--execute", action="store_true",
                      help="write the audit-chain marker + sealed record file")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    dry_run = not args.execute
    record = run_redaction(
        tenant_id=args.tenant,
        legal_reason=args.reason,
        actor=args.actor,
        audit_dsn=args.audit_db,
        output_dir=args.output_dir,
        dry_run=dry_run,
    )
    print(json.dumps(record.to_dict(), indent=2, sort_keys=True))
    if dry_run:
        print(f"[redact] DRY-RUN — would redact {len(record.fields)} field(s)", file=sys.stderr)
    else:
        print(f"[redact] wrote chain marker {record.audit_chain_marker_row_id}", file=sys.stderr)
        print(f"[redact] sealed record: {args.output_dir}/{record.redaction_id}.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
