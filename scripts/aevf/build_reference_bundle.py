#!/usr/bin/env python3
"""Build the AEVF Reference Evidence Package.

This script produces `docs/AEVF/reference-bundle-2026-06.json` — a real,
deterministic, signed AEVF bundle that an auditor anywhere in the world
can download and run `aegis-verify --bundle …` against.

The bundle is **byte-deterministic**: running this script on any
machine, at any time, must produce the same SHA-256 (assuming the same
cryptography library version). That property is the whole point — an
auditor can compare hashes across firms and across time, and any
divergence in bytes is a divergence in the generator.

Determinism contract:
  1. The ed25519 signing keypair is derived from a fixed seed
     (`AEVF_REFERENCE_BUNDLE_SEED_2026_06`). DO NOT use this key for
     anything other than the reference bundle.
  2. All timestamps are fixed literal strings.
  3. All UUIDs are fixed hex strings.
  4. JSON serialization uses the canonical forms defined in
     `docs/AEVF/spec.md` §4 (compact for signatures, default-separators
     for event_hash).

Run:
    python3 scripts/aevf/build_reference_bundle.py [--out PATH]

Verify with the reference implementation:
    aegis-verify --bundle docs/AEVF/reference-bundle-2026-06.json
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


# ─── Determinism knobs ─────────────────────────────────────────────────────

# Ed25519 seed — 32 bytes derived from a fixed UTF-8 string so the
# keypair is identical on every run. **DO NOT REUSE** outside the
# reference bundle.
_SEED_STR = "AEVF_REFERENCE_BUNDLE_SEED_2026_06"
_SEED = hashlib.sha256(_SEED_STR.encode("utf-8")).digest()

TENANT_ID = "11111111-1111-1111-1111-111111111111"

# Day-1 = 2026-06-12, Day-2 = 2026-06-13. The bundle period spans both.
DAY_1 = date(2026, 6, 12)
DAY_2 = date(2026, 6, 13)

GENESIS_HASH = "0" * 64


# ─── Canonical JSON (must match docs/AEVF/spec.md §4) ──────────────────────

def canonical_compact(obj: Any) -> bytes:
    """Form A — compact, used for signatures."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def canonical_default(obj: Any) -> bytes:
    """Form B — default separators (with spaces), used for event_hash."""
    return json.dumps(obj, sort_keys=True).encode("utf-8")


def url_b64_no_pad(b: bytes) -> str:
    """URL-safe base64 without padding (spec §3)."""
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


# ─── Event hash recipe (spec §7.1) ─────────────────────────────────────────

def compute_event_hash(
    prev_hash: str, tenant_id: str, agent_id: str,
    action: str, tool: str | None, decision: str, request_id: str | None,
) -> str:
    payload = canonical_default(
        {
            "tenant_id":  str(tenant_id),
            "agent_id":   str(agent_id),
            "action":     str(action),
            "tool":       str(tool or ""),
            "decision":   str(decision),
            "request_id": str(request_id or ""),
        }
    )
    return hashlib.sha256(prev_hash.encode("utf-8") + payload).hexdigest()


# ─── Public-key fingerprint (spec §6) ──────────────────────────────────────

def fingerprint(pem_bytes: bytes) -> str:
    return hashlib.sha256(pem_bytes).hexdigest()[:32]


# ─── Merkle (spec §9) ──────────────────────────────────────────────────────

EMPTY_ROOT = hashlib.sha256(b"").hexdigest()


def leaf_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_root(leaves_hex: list[str]) -> str:
    if not leaves_hex:
        return EMPTY_ROOT
    level = [bytes.fromhex(h) for h in leaves_hex]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        level = [
            hashlib.sha256(level[i] + level[i + 1]).digest()
            for i in range(0, len(level), 2)
        ]
    return level[0].hex()


# ─── Records — hand-crafted to showcase R0/R5 + the v3 thesis ─────────────
#
# Each row tells a story an auditor can follow. Mappings cite EU AI Act,
# SOC 2, and India DPDP control IDs.

# Day 1 (2026-06-12) — three rows:
#   row 0: allowed read of a config file (mundane baseline)
#   row 1: DENIED bulk PII export (R0+R5 fintech scenario)
#   row 2: allowed audit trail query (just an analytics read)
#
# Day 2 (2026-06-13) — two rows:
#   row 3: DENIED kubectl delete ns prod (R0+R5 devops scenario)
#   row 4: ESCALATED external PII email (R0+R5 support scenario, human approval needed)

RAW_RECORDS: list[dict[str, Any]] = [
    # Day 1, row 0 — benign read
    {
        "id":            "aaaaaaaa-0000-4000-8000-000000000001",
        "tenant_id":     TENANT_ID,
        "agent_id":      "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "action":        "execute_tool",
        "tool":          "tool.read_file",
        "decision":      "allow",
        "reason":        "",
        "request_id":    "req-day1-001",
        "timestamp":     "2026-06-12T09:14:00.000000+00:00",
        "metadata_json": {"path": "/app/config/feature_flags.json", "risk_score": 0.05},
        "chain_shard":   0,
        "mappings": {
            "eu_ai_act":   ["Article 12"],
            "soc2":        ["CC7.2"],
            "nist_ai_rmf": ["MEASURE 2.7"],
            "dpdp":        ["Section 8(5)"],
        },
    },
    # Day 1, row 1 — R5 fintech scenario, DENIED
    {
        "id":            "aaaaaaaa-0000-4000-8000-000000000002",
        "tenant_id":     TENANT_ID,
        "agent_id":      "22222222-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "action":        "execute_tool",
        "tool":          "tool.sql_query",
        "decision":      "deny",
        "reason":        "bulk_pii_egress_above_threshold",
        "request_id":    "req-day1-002",
        "timestamp":     "2026-06-12T11:42:13.000000+00:00",
        "metadata_json": {
            "query":         "SELECT * FROM customers",
            "agent_risk":    "medium",
            "risk_score":    0.92,
            "policy_rule":   "action_semantics_deny._pii_row_threshold_breached",
        },
        "chain_shard":   0,
        "mappings": {
            "eu_ai_act":   ["Article 12", "Article 13", "Article 14"],
            "soc2":        ["CC6.1", "CC7.2"],
            "nist_ai_rmf": ["MEASURE 2.7", "MANAGE 4.2"],
            "dpdp":        ["Section 8(5)", "Section 8(6)"],
        },
    },
    # Day 1, row 2 — benign analytics read
    {
        "id":            "aaaaaaaa-0000-4000-8000-000000000003",
        "tenant_id":     TENANT_ID,
        "agent_id":      "11111111-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "action":        "execute_tool",
        "tool":          "tool.sql_query",
        "decision":      "allow",
        "reason":        "",
        "request_id":    "req-day1-003",
        "timestamp":     "2026-06-12T17:05:09.000000+00:00",
        "metadata_json": {
            "query":      "SELECT count(*) FROM audit_logs WHERE created_at > now() - interval '1 day'",
            "risk_score": 0.1,
        },
        "chain_shard":   0,
        "mappings": {
            "eu_ai_act":   ["Article 12"],
            "soc2":        ["CC7.2"],
            "nist_ai_rmf": ["MEASURE 2.7"],
            "dpdp":        ["Section 8(5)"],
        },
    },
    # Day 2, row 3 — R5 devops scenario, DENIED prod-namespace destruction
    {
        "id":            "bbbbbbbb-0000-4000-8000-000000000004",
        "tenant_id":     TENANT_ID,
        "agent_id":      "33333333-cccc-4ccc-8ccc-cccccccccccc",
        "action":        "execute_tool",
        "tool":          "tool.shell",
        "decision":      "deny",
        "reason":        "k8s_prod_namespace_destruction",
        "request_id":    "req-day2-001",
        "timestamp":     "2026-06-13T08:21:55.000000+00:00",
        "metadata_json": {
            "command":       "kubectl delete ns prod --force",
            "k8s_namespace": "prod",
            "agent_risk":    "low",
            "risk_score":    0.95,
            "policy_rule":   "action_semantics_deny._k8s_prod_destruction",
        },
        "chain_shard":   0,
        "mappings": {
            "eu_ai_act":   ["Article 12", "Article 14"],
            "soc2":        ["CC6.1", "CC7.2", "CC8.1"],
            "nist_ai_rmf": ["MEASURE 2.7", "MANAGE 4.2"],
            "dpdp":        ["Section 8(5)"],
        },
    },
    # Day 2, row 4 — R5 support scenario, ESCALATED for human override
    {
        "id":            "bbbbbbbb-0000-4000-8000-000000000005",
        "tenant_id":     TENANT_ID,
        "agent_id":      "44444444-dddd-4ddd-8ddd-dddddddddddd",
        "action":        "execute_tool",
        "tool":          "tool.http_request",
        "decision":      "escalate",
        "reason":        "external_pii_exfil",
        "request_id":    "req-day2-002",
        "timestamp":     "2026-06-13T14:33:21.000000+00:00",
        "metadata_json": {
            "url":           "https://api.external-vendor.com/sync",
            "body_excerpt":  "POST customer list with email and phone columns",
            "agent_risk":    "medium",
            "risk_score":    0.78,
            "policy_rule":   "action_semantics_deny._external_exfil",
            "operator_action_required": True,
        },
        "chain_shard":   0,
        "mappings": {
            "eu_ai_act":   ["Article 12", "Article 14"],
            "soc2":        ["CC6.1", "CC7.2"],
            "nist_ai_rmf": ["MEASURE 2.7", "MAP 3.1"],
            "dpdp":        ["Section 8(5)", "Section 8(8)"],
        },
    },
]


def build_bundle() -> dict[str, Any]:
    # ── 1. Keypair (deterministic) ─────────────────────────────────────────
    sk = Ed25519PrivateKey.from_private_bytes(_SEED)
    pk = sk.public_key()
    pem = pk.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pem_str = pem.decode("ascii")
    kid = fingerprint(pem)

    # ── 2. Compute event_hash + prev_hash chain (spec §7.1, §8) ────────────
    records_out: list[dict[str, Any]] = []
    prev_hash = GENESIS_HASH
    for raw in RAW_RECORDS:
        eh = compute_event_hash(
            prev_hash=prev_hash,
            tenant_id=raw["tenant_id"],
            agent_id=raw["agent_id"],
            action=raw["action"],
            tool=raw["tool"],
            decision=raw["decision"],
            request_id=raw["request_id"],
        )
        audit_row = {
            "id":            raw["id"],
            "tenant_id":     raw["tenant_id"],
            "agent_id":      raw["agent_id"],
            "action":        raw["action"],
            "tool":          raw["tool"],
            "decision":      raw["decision"],
            "reason":        raw["reason"],
            "metadata_json": raw["metadata_json"],
            "request_id":    raw["request_id"],
            "timestamp":     raw["timestamp"],
            "chain_shard":   raw["chain_shard"],
            "prev_hash":     prev_hash,
            "event_hash":    eh,
        }
        # Determine which day's Merkle root this row belongs to
        ts_day = raw["timestamp"][:10]
        records_out.append({
            "audit_row":        audit_row,
            "mappings":         raw["mappings"],
            "merkle_root_date": ts_day,
        })
        prev_hash = eh

    # ── 3. Compute leaf hashes per row (spec §9.1) ─────────────────────────
    # The "leaf" is sha256(canonical_compact(<signed receipt>)). Here we
    # treat the audit_row dict as the receipt body — the producer is the
    # signer, and what's signed is the row as it lands.
    leaves_by_day: dict[str, list[tuple[str, str]]] = {}  # day -> [(audit_id, leaf_hex)]
    for rec in records_out:
        row = rec["audit_row"]
        day = row["timestamp"][:10]
        leaf_payload = canonical_compact(row)
        leaves_by_day.setdefault(day, []).append((row["id"], leaf_hash(leaf_payload)))

    # ── 4. Per-day Merkle root + signature (spec §9, §11) ──────────────────
    merkle_roots: list[dict[str, Any]] = []
    prev_root_hash: str | None = None
    for day in sorted(leaves_by_day):
        leaves = leaves_by_day[day]
        # Sort by (timestamp asc, id asc) — here timestamps within a day are
        # unique per row by construction, so id sort is sufficient.
        leaves_sorted = sorted(leaves, key=lambda kv: kv[0])
        leaf_hexes = [h for _, h in leaves_sorted]
        root_hex = build_root(leaf_hexes)

        receipt = {
            "kind":               "transparency_root",
            "version":            4,
            "tenant_id":          TENANT_ID,
            "root_date":          day,
            "root_hash":          root_hex,
            "leaf_count":         len(leaves_sorted),
            "leaf_range_start_id": leaves_sorted[0][0],
            "leaf_range_end_id":   leaves_sorted[-1][0],
            "prev_root_hash":     prev_root_hash,
        }
        payload = canonical_compact(receipt)
        sig = sk.sign(payload)
        sig_b64 = url_b64_no_pad(sig)

        merkle_roots.append({
            "root_date":           day,
            "root_hash":           root_hex,
            "leaf_count":          len(leaves_sorted),
            "leaf_range_start_id": leaves_sorted[0][0],
            "leaf_range_end_id":   leaves_sorted[-1][0],
            "prev_root_hash":      prev_root_hash,
            "kid":                 kid,
            "algorithm":           "ed25519",
            "signature_b64":       sig_b64,
            "signed_payload_canonical_json": payload.decode("utf-8"),
        })
        prev_root_hash = root_hex

    earliest_ts = records_out[0]["audit_row"]["timestamp"]
    latest_ts   = records_out[-1]["audit_row"]["timestamp"]

    # ── 5. Bundle envelope ─────────────────────────────────────────────────
    bundle = {
        "format_version": "aegis-evidence-bundle/2026-06",
        "framework":      "eu-ai-act",
        "tenant_id":      TENANT_ID,
        "period":         {"start": "2026-06-12", "end": "2026-06-13"},
        "generated_at":   "2026-06-14T00:00:00+00:00",
        "public_keys": [{
            "kid":         kid,
            "algorithm":   "ed25519",
            "pem":         pem_str,
            "valid_from":  "2026-01-01T00:00:00+00:00",
            "valid_to":    None,
        }],
        "merkle_roots":   merkle_roots,
        "records":        records_out,
        "retention_metadata": {
            "policy":                     "6_months_minimum",
            "configured_retention_days":  180,
            "earliest_row_in_bundle":     earliest_ts,
            "latest_row_in_bundle":       latest_ts,
        },
        "producer_metadata": {
            "name":          "AEVF Reference Generator",
            "purpose":       "Reference Evidence Package for AEVF v0.1.0",
            "deterministic": True,
            "seed_source":   "sha256(AEVF_REFERENCE_BUNDLE_SEED_2026_06)",
        },
    }
    return bundle


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the AEVF reference bundle.")
    repo_root = Path(__file__).resolve().parents[2]
    default_out = repo_root / "docs" / "AEVF" / "reference-bundle-2026-06.json"
    p.add_argument("--out", type=Path, default=default_out,
                   help=f"output path (default: {default_out})")
    p.add_argument("--print-hash", action="store_true",
                   help="print SHA-256 of the generated file")
    args = p.parse_args(argv)

    bundle = build_bundle()
    body = json.dumps(bundle, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    print(f"wrote: {args.out}")
    print(f"size:  {len(body)} bytes")
    print(f"sha256:{digest}")
    if args.print_hash:
        print(f"\n{digest}  {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
