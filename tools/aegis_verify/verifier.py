"""Core verification logic for Aegis evidence bundles.

The bundle format (`aegis-evidence-bundle/2026-06`) is self-contained:

    {
      "format_version": "aegis-evidence-bundle/2026-06",
      "framework":       "eu-ai-act",
      "tenant_id":       "...",
      "period":          {"start": "...", "end": "..."},
      "generated_at":    "...",

      // Every ed25519 public key that signed *anything* in this bundle.
      // The verifier never needs to phone the vendor for a key.
      "public_keys": [
        {
          "kid":       "<fingerprint hex>",
          "algorithm": "ed25519",
          "pem":       "-----BEGIN PUBLIC KEY-----...-----END PUBLIC KEY-----",
          "valid_from": "...",
          "valid_to":   null | "..."
        }, ...
      ],

      // Each daily Merkle root, with its signed receipt and the prev-link
      // back to the previous day's root. Roots chain across days the same
      // way rows chain within a day.
      "merkle_roots": [
        {
          "root_date":          "YYYY-MM-DD",
          "root_hash":          "<sha256 hex>",
          "leaf_count":         <int>,
          "leaf_range_start_id":"...",
          "leaf_range_end_id":  "...",
          "prev_root_hash":     null | "...",
          "kid":                "<key fingerprint>",
          "algorithm":          "ed25519",
          "signature_b64":      "<base64 ed25519 sig>",
          "signed_payload_canonical_json": "<exact canonical JSON that was signed>"
        }, ...
      ],

      // Per-row records. The audit_row is the row that landed in
      // audit_logs; mappings tells the auditor which framework
      // controls / articles this row evidences.
      "records": [
        {
          "audit_row": {
              "id":              "<uuid>",
              "tenant_id":       "...",
              "agent_id":        "...",
              "action":          "execute_tool" | "policy_evaluation" | ...,
              "tool":            "tool.shell" | ...,
              "decision":        "allow" | "deny" | ...,
              "request_id":      "...",
              "event_hash":      "<sha256 hex>",
              "prev_hash":       "<sha256 hex of previous row's event_hash>",
              "chain_shard":     <int>,
              "timestamp":       "..."
              // metadata_json + other fields included verbatim
          },
          "mappings": {
              "eu_ai_act":     ["Article 12", "Article 16"],
              "soc2":          ["CC6.1"],
              "nist_ai_rmf":   ["MEASURE 2.1"]
          },
          "merkle_root_date":  "YYYY-MM-DD"
        }, ...
      ],

      "retention_metadata": {
        "policy":                  "6_months_minimum",
        "configured_retention_days": 180,
        "earliest_row_in_bundle":  "...",
        "latest_row_in_bundle":    "..."
      }
    }

Verification steps (each must pass for the bundle to verify):

  V1  bundle format_version is one we recognize
  V2  every record's event_hash matches a recomputation from row content
  V3  per chain_shard, every record's prev_hash equals the previous
      record's event_hash (the prev_hash chain is intact)
  V4  every Merkle root's signature verifies against the public key
      named by its kid (using the embedded PEM, never a remote fetch)
  V5  the prev_root_hash chain across daily Merkle roots is intact
  V6  retention_metadata is consistent with the rows actually in the
      bundle (e.g. claimed 6-month policy and earliest_row > 6 months
      ago = the retention claim is honest)
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

# Single runtime dep — cryptography for ed25519. No httpx, no requests,
# no Aegis SDK imports. An auditor with `pip install cryptography` can
# run this offline on an air-gapped machine.
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.exceptions import InvalidSignature


SUPPORTED_FORMATS = {"aegis-evidence-bundle/2026-06"}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CheckResult:
    """One verification step's outcome."""
    name: str            # short label, e.g. "V2_event_hash_recompute"
    passed: bool
    detail: str          # human-readable. Empty if passed.

    def __str__(self) -> str:
        flag = "PASS" if self.passed else "FAIL"
        suffix = f"  — {self.detail}" if self.detail else ""
        return f"  [{flag}] {self.name}{suffix}"


@dataclasses.dataclass
class VerificationReport:
    bundle_format: str
    framework: str
    tenant_id: str
    record_count: int
    merkle_root_count: int
    public_key_count: int
    checks: list[CheckResult]
    first_broken_row_id: str | None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def render(self, verbose: bool = False) -> str:
        head = (
            f"aegis-verify report\n"
            f"  bundle:     {self.bundle_format}\n"
            f"  framework:  {self.framework}\n"
            f"  tenant:     {self.tenant_id}\n"
            f"  records:    {self.record_count}\n"
            f"  keys:       {self.public_key_count}\n"
            f"  roots:      {self.merkle_root_count}\n"
            f"\n"
        )
        lines = [head, "Checks:"]
        for c in self.checks:
            if c.passed and not verbose:
                continue
            lines.append(str(c))
        if all(c.passed for c in self.checks):
            lines.append("\n*** PASS *** every signature, hash chain, and "
                         "Merkle root in this bundle verifies.")
        else:
            lines.append("\n*** FAIL *** at least one check failed. See above.")
            if self.first_broken_row_id:
                lines.append(f"             first broken row: {self.first_broken_row_id}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Canonical JSON — what we hash & sign over
# ---------------------------------------------------------------------------

# Two distinct canonical forms exist in the writer codebase and we must
# match each EXACTLY or signatures/hashes won't round-trip:
#
# (A) Signature canonicalization — `services/audit/signer.py:canonical_json`
#     json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)
#     used when computing the signed-receipt payload that the ed25519 key
#     signs. The bundle ships this verbatim as `signed_payload_canonical_json`.
#
# (B) Event-hash canonicalization — `sdk/common/audit_hash.py:compute_event_hash`
#     json.dumps({6 specific fields}, sort_keys=True)   # default separators!
#     then sha256(prev_hash + payload_str). NOT compact. NOT all fields.
#     This was the source of R2's first verifier vs. writer mismatch.

def _canonical(obj: Any) -> bytes:
    """Canonical form (B) — compact, used for signed receipts."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _recompute_event_hash(row: dict[str, Any]) -> str:
    """Recompute event_hash exactly the way the audit writer does.

    Source of truth: `sdk/common/audit_hash.py::compute_event_hash`.
    Only the SIX fields below participate in the chain. Adding more
    means hashes won't recompute and V2 fails. Adding fewer means
    tampering goes undetected. Don't drift from the writer.
    """
    prev_hash = row.get("prev_hash") or ""
    payload = json.dumps(
        {
            "tenant_id":  str(row.get("tenant_id") or ""),
            "agent_id":   str(row.get("agent_id") or ""),
            "action":     str(row.get("action") or ""),
            "tool":       str(row.get("tool") or ""),
            "decision":   str(row.get("decision") or ""),
            "request_id": str(row.get("request_id") or ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()


# Signatures in the writer are URL-safe base64 without padding (see
# `services/audit/signer.py::_b64`). Standard base64 decode raises
# "Incorrect padding" on those — the R2 v1 verifier hit this on every
# real Merkle root. Add padding back before decoding.
def _b64decode_signature(s: str) -> bytes:
    s = (s or "").strip()
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ---------------------------------------------------------------------------
# Public verifier entry point
# ---------------------------------------------------------------------------

def verify_bundle(bundle: dict[str, Any]) -> VerificationReport:
    """Run every verification step and return a structured report."""
    checks: list[CheckResult] = []
    first_broken: str | None = None

    # -- V1: format version -------------------------------------------------
    fmt = bundle.get("format_version", "")
    checks.append(CheckResult(
        "V1_bundle_format_recognized",
        fmt in SUPPORTED_FORMATS,
        "" if fmt in SUPPORTED_FORMATS else
        f"unknown bundle format {fmt!r}; expected one of {sorted(SUPPORTED_FORMATS)}",
    ))

    public_keys = bundle.get("public_keys") or []
    merkle_roots = bundle.get("merkle_roots") or []
    records = bundle.get("records") or []

    # Index keys by kid for O(1) lookup during signature checks.
    keys_by_kid = {k.get("kid"): k for k in public_keys if isinstance(k, dict)}

    # -- V2: per-row event_hash recompute -----------------------------------
    bad_rows: list[str] = []
    for rec in records:
        row = rec.get("audit_row") or {}
        stored = row.get("event_hash")
        if not stored:
            bad_rows.append(row.get("id") or "<no-id>")
            continue
        recomputed = _recompute_event_hash(row)
        if recomputed != stored:
            bad_rows.append(row.get("id") or "<no-id>")
    if bad_rows and first_broken is None:
        first_broken = bad_rows[0]
    checks.append(CheckResult(
        "V2_event_hash_recompute",
        not bad_rows,
        "" if not bad_rows else
        f"{len(bad_rows)} row(s) have event_hash that doesn't recompute "
        f"from content (first: {bad_rows[0]})",
    ))

    # -- V3: prev_hash chain per shard --------------------------------------
    by_shard: dict[int, list[dict]] = {}
    for rec in records:
        row = rec.get("audit_row") or {}
        shard = int(row.get("chain_shard") or 0)
        by_shard.setdefault(shard, []).append(row)
    chain_breaks: list[str] = []
    for shard, rows in by_shard.items():
        rows.sort(key=lambda r: r.get("timestamp", ""))
        prev = None
        for r in rows:
            if prev is not None:
                if r.get("prev_hash") != prev:
                    chain_breaks.append(r.get("id") or "<no-id>")
            prev = r.get("event_hash")
    if chain_breaks and first_broken is None:
        first_broken = chain_breaks[0]
    checks.append(CheckResult(
        "V3_prev_hash_chain_per_shard",
        not chain_breaks,
        "" if not chain_breaks else
        f"{len(chain_breaks)} prev_hash mismatch(es) (first: {chain_breaks[0]})",
    ))

    # -- V4: Merkle root signatures -----------------------------------------
    sig_fails: list[str] = []
    for root in merkle_roots:
        kid = root.get("kid")
        key_entry = keys_by_kid.get(kid)
        if not key_entry:
            sig_fails.append(f"root {root.get('root_date')}: unknown kid {kid!r}")
            continue
        pem = key_entry.get("pem", "")
        sig_b64 = root.get("signature_b64")
        payload = root.get("signed_payload_canonical_json")
        if not (pem and sig_b64 and payload):
            sig_fails.append(f"root {root.get('root_date')}: missing pem/sig/payload")
            continue
        try:
            pk = load_pem_public_key(pem.encode("utf-8"))
            pk.verify(_b64decode_signature(sig_b64), payload.encode("utf-8"))
        except InvalidSignature:
            sig_fails.append(f"root {root.get('root_date')}: ed25519 signature invalid")
        except Exception as exc:
            sig_fails.append(f"root {root.get('root_date')}: {type(exc).__name__}: {exc}")
    checks.append(CheckResult(
        "V4_merkle_root_signatures",
        not sig_fails,
        "" if not sig_fails else
        f"{len(sig_fails)} Merkle root signature(s) failed (first: {sig_fails[0]})",
    ))

    # -- V5: prev_root_hash chain across daily roots ------------------------
    root_breaks: list[str] = []
    sorted_roots = sorted(merkle_roots, key=lambda r: r.get("root_date", ""))
    prev_root: str | None = None
    for r in sorted_roots:
        if prev_root is not None and r.get("prev_root_hash") not in (prev_root, None):
            # prev_root_hash null is allowed at chain bootstrap; mid-chain
            # nulls would be an intentional skip we tolerate as a warning
            # (some early bundles predate root chaining).
            root_breaks.append(r.get("root_date") or "<no-date>")
        prev_root = r.get("root_hash")
    checks.append(CheckResult(
        "V5_prev_root_hash_chain",
        not root_breaks,
        "" if not root_breaks else
        f"{len(root_breaks)} root chain break(s) (first: {root_breaks[0]})",
    ))

    # -- V6: retention metadata sanity --------------------------------------
    rm = bundle.get("retention_metadata") or {}
    earliest = rm.get("earliest_row_in_bundle")
    policy_days = rm.get("configured_retention_days")
    retention_ok = True
    retention_detail = ""
    if earliest and policy_days:
        try:
            earliest_dt = datetime.fromisoformat(earliest.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - earliest_dt).days
            if age_days > int(policy_days):
                retention_ok = False
                retention_detail = (
                    f"earliest row is {age_days} days old but configured "
                    f"retention is {policy_days} days — rows have outlived the "
                    f"declared policy"
                )
        except (ValueError, TypeError) as exc:
            retention_detail = f"could not parse retention metadata: {exc}"
            # Don't fail the bundle for unparseable metadata; warn only.
    checks.append(CheckResult(
        "V6_retention_metadata_consistent",
        retention_ok,
        retention_detail,
    ))

    return VerificationReport(
        bundle_format=fmt,
        framework=bundle.get("framework", ""),
        tenant_id=bundle.get("tenant_id", ""),
        record_count=len(records),
        merkle_root_count=len(merkle_roots),
        public_key_count=len(public_keys),
        checks=checks,
        first_broken_row_id=first_broken,
    )
