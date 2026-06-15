"""Synthesize healthy + tampered bundles, prove the verifier catches each tamper.

These tests have zero dependency on a running Aegis instance — they
generate their own ed25519 keypairs and craft bundles directly. The
point: the verifier is purely functional. An auditor can rerun these
tests on their own laptop.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PublicFormat,
)

from aegis_verify.verifier import (
    SUPPORTED_FORMATS,
    _canonical,
    _recompute_event_hash,
    verify_bundle,
)


# --------------------------------------------------------------------------
# Synthetic bundle construction — keep this in lockstep with what the
# audit-service writer produces. If the writer's canonicalization
# changes, these tests will fail (which is correct — the verifier is
# the contract).
# --------------------------------------------------------------------------

def _make_keypair():
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    pem = pk.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
    fp = hashlib.md5(pem.encode()).hexdigest()[:32]  # not security-meaningful, kid only
    return sk, pem, fp


def _make_row(
    *,
    row_id: str,
    prev_hash: str,
    chain_shard: int,
    tenant_id: str = "test-tenant",
    decision: str = "allow",
    tool: str = "tool.read_file",
    timestamp: str | None = None,
) -> dict[str, Any]:
    base = {
        "id":            row_id,
        "tenant_id":     tenant_id,
        "agent_id":      "agent-1",
        "action":        "execute_tool",
        "tool":          tool,
        "decision":      decision,
        "reason":        "",
        "metadata_json": {"risk_score": 0.0},
        "request_id":    f"req-{row_id[:8]}",
        "timestamp":     timestamp or "2026-06-13T12:00:00.000000+00:00",
        "chain_shard":   chain_shard,
        "prev_hash":     prev_hash,
    }
    base["event_hash"] = _recompute_event_hash(base)
    return base


def _sign_root(sk, root_receipt: dict[str, Any]) -> tuple[str, str]:
    """Sign a receipt the way the audit writer does: canonical_json +
    URL-safe base64 without padding."""
    payload = _canonical(root_receipt)
    sig = sk.sign(payload)
    return payload.decode(), base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def _make_bundle(*, days: int = 2, rows_per_day: int = 3) -> dict[str, Any]:
    """A healthy bundle for `days` days, `rows_per_day` rows each, one shard."""
    sk, pem, fp = _make_keypair()
    today = datetime(2026, 6, 13, tzinfo=timezone.utc)

    records: list[dict[str, Any]] = []
    roots: list[dict[str, Any]] = []
    prev_root_hash: str | None = None
    prev_hash: str = "0" * 64  # genesis

    for d in range(days):
        day_date = (today - timedelta(days=days - 1 - d)).date().isoformat()
        day_rows = []
        for i in range(rows_per_day):
            ts = f"{day_date}T12:{i:02d}:00.000000+00:00"
            row = _make_row(
                row_id=f"row-{d:02d}-{i:02d}",
                prev_hash=prev_hash,
                chain_shard=0,
                timestamp=ts,
            )
            prev_hash = row["event_hash"]
            day_rows.append(row)
            records.append({
                "audit_row":        row,
                "mappings": {
                    "eu_ai_act":   ["Article 12"],
                    "soc2":        ["CC6.1"],
                    "nist_ai_rmf": ["MEASURE 2.1"],
                },
                "merkle_root_date": day_date,
            })
        # Day's Merkle root = sha256 of concatenated event_hashes (simplified).
        leaves = "".join(r["event_hash"] for r in day_rows).encode()
        root_hash = hashlib.sha256(leaves).hexdigest()
        receipt = {
            "kind":               "transparency_root",
            "version":            4,
            "root_date":          day_date,
            "root_hash":          root_hash,
            "tenant_id":          "test-tenant",
            "leaf_count":         len(day_rows),
            "leaf_range_start_id": day_rows[0]["id"],
            "leaf_range_end_id":   day_rows[-1]["id"],
            "prev_root_hash":     prev_root_hash,
        }
        canonical_payload, sig_b64 = _sign_root(sk, receipt)
        roots.append({
            **receipt,
            "kid":                            fp,
            "algorithm":                      "ed25519",
            "signature_b64":                  sig_b64,
            "signed_payload_canonical_json":  canonical_payload,
        })
        prev_root_hash = root_hash

    return {
        "format_version": "aegis-evidence-bundle/2026-06",
        "framework":      "eu-ai-act",
        "tenant_id":      "test-tenant",
        "period":         {"start": "2026-06-12", "end": "2026-06-13"},
        "generated_at":   "2026-06-13T23:00:00+00:00",
        "public_keys": [{
            "kid":        fp,
            "algorithm":  "ed25519",
            "pem":        pem,
            "valid_from": "2026-01-01T00:00:00+00:00",
            "valid_to":   None,
        }],
        "merkle_roots": roots,
        "records":      records,
        "retention_metadata": {
            "policy":                     "6_months_minimum",
            "configured_retention_days":  180,
            "earliest_row_in_bundle":     records[0]["audit_row"]["timestamp"],
            "latest_row_in_bundle":       records[-1]["audit_row"]["timestamp"],
        },
    }


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_healthy_bundle_passes():
    """The baseline — a clean bundle verifies fully."""
    bundle = _make_bundle()
    report = verify_bundle(bundle)
    assert report.passed, report.render(verbose=True)
    assert all(c.passed for c in report.checks)


def test_unknown_format_fails():
    bundle = _make_bundle()
    bundle["format_version"] = "totally-made-up-format/9999"
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V1_bundle_format_recognized" for c in report.checks)


def test_row_tampered_event_hash_fails():
    """Edit one row's decision after the fact — V2 must catch it."""
    bundle = _make_bundle()
    bundle["records"][1]["audit_row"]["decision"] = "tampered_value"
    # event_hash left in place → recompute won't match
    report = verify_bundle(bundle)
    assert not report.passed
    failed = [c for c in report.checks if not c.passed]
    assert any(c.name == "V2_event_hash_recompute" for c in failed)
    assert report.first_broken_row_id is not None


def test_chain_break_fails():
    """Skip a row (delete a middle record): the next row's prev_hash no longer points anywhere — V3 catches it."""
    bundle = _make_bundle(days=1, rows_per_day=4)
    # delete record index 1 → record at index 2 now has a prev_hash that
    # references the deleted row's event_hash, not record 0's.
    del bundle["records"][1]
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V3_prev_hash_chain_per_shard" for c in report.checks)


def test_merkle_signature_forged_fails():
    """Replace one Merkle root's signature with garbage — V4 catches it."""
    bundle = _make_bundle(days=2)
    bundle["merkle_roots"][1]["signature_b64"] = base64.b64encode(b"\x00" * 64).decode()
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V4_merkle_root_signatures" for c in report.checks)


def test_unknown_kid_fails():
    bundle = _make_bundle()
    bundle["merkle_roots"][0]["kid"] = "no-such-key-fingerprint"
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V4_merkle_root_signatures" for c in report.checks)


def test_root_chain_break_fails():
    """Change one root's prev_root_hash to break the cross-day chain."""
    bundle = _make_bundle(days=3)
    bundle["merkle_roots"][2]["prev_root_hash"] = "0" * 64
    # Note: V5 checks chain across roots; we must NOT re-sign the receipt
    # because then V4 would pass and V5 would catch it. With current
    # signed_payload_canonical_json kept intact, the signature stays
    # valid for the old prev_root_hash but the json field shows the new
    # value. V5 reads the json field directly.
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V5_prev_root_hash_chain" for c in report.checks)


def test_retention_dishonest_fails():
    """Claim 30-day retention but earliest row is years old."""
    bundle = _make_bundle()
    bundle["retention_metadata"]["configured_retention_days"] = 30
    bundle["retention_metadata"]["earliest_row_in_bundle"] = "2020-01-01T00:00:00+00:00"
    report = verify_bundle(bundle)
    assert not report.passed
    assert any(not c.passed and c.name == "V6_retention_metadata_consistent" for c in report.checks)


def test_supported_formats_constant_is_documented():
    """If we bump the format, the README claim must keep pace — soft check."""
    assert SUPPORTED_FORMATS == {"aegis-evidence-bundle/2026-06"}


def test_canonical_json_matches_aegis_writer():
    """The verifier and the audit-writer must canonicalize identically.

    If the writer ever switches to a different canonical form (e.g. RFC8785
    instead of sort_keys), bundles produced AFTER the switch won't verify
    by bundles produced BEFORE the switch. This test pins the spec.
    """
    obj = {"b": 2, "a": 1, "nested": {"y": 4, "x": 3}}
    assert _canonical(obj) == b'{"a":1,"b":2,"nested":{"x":3,"y":4}}'
