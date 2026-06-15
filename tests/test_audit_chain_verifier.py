"""
Sprint 1.1 — adversarial tests for the audit-chain verifier.

The audit (C9) finding was that ``acp verify-chain`` previously walked only
the daily-root chain — a tampered receipt would slip through unnoticed. After
this sprint the verifier:

  1. Verifies ed25519 signatures on every receipt against current and
     historical keys.
  2. Walks the per-shard prev_hash linkage independently of the signature.
  3. Recomputes event_hash from the receipt body and compares to the claimed
     value — catches in-place mutation of the hash field even when the
     attacker controls the signing key.
  4. Surfaces tail truncation (unanchored receipts).
  5. Walks the daily-root chain.

These tests synthesise a complete export bundle in a temp dir, then mutate it
in each of the four ways the sprint promised to detect:

  * flipped byte in event_hash
  * deleted (truncated) row
  * swapped signing key (attacker key passes signature but is unknown to verifier)
  * intra-shard prev_hash rewrite
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sdk.acp_client.verifier import (
    AuditVerifier,
    detect_truncation,
    leaf_hash,
    recompute_event_hash,
    verify_shard_chains,
)
from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash


# ---------------------------------------------------------------------------
# Bundle synthesis helpers
# ---------------------------------------------------------------------------


def _make_keypair() -> tuple[ed25519.Ed25519PrivateKey, str]:
    priv = ed25519.Ed25519PrivateKey.generate()
    pub_pem = priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return priv, pub_pem


def _fingerprint(pub_pem: str) -> str:
    import hashlib
    return hashlib.sha256(pub_pem.encode("ascii")).hexdigest()[:32]


def _canonical_json(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64(b: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _build_receipt(
    *,
    execution_id: str,
    tenant_id: str,
    agent_id: str,
    action: str,
    tool: str | None,
    decision: str,
    request_id: str,
    timestamp: str,
    event_hash: str,
    prev_hash: str,
    chain_shard: int,
) -> dict[str, Any]:
    return {
        "version":      1,
        "execution_id": execution_id,
        "tenant_id":    tenant_id,
        "agent_id":     agent_id,
        "tool":         tool,
        "action":       action,
        "decision":     decision,
        "reason":       None,
        "request_id":   request_id,
        "timestamp":    timestamp,
        "event_hash":   event_hash,
        "prev_hash":    prev_hash,
        "chain_shard":  chain_shard,
    }


def _sign(receipt: dict[str, Any], priv: ed25519.Ed25519PrivateKey, fp: str) -> dict[str, Any]:
    sig = priv.sign(_canonical_json(receipt))
    return {
        "receipt":                receipt,
        "signature":              _b64(sig),
        "algorithm":              "ed25519",
        "public_key_fingerprint": fp,
    }


def _build_inclusion_proof(leaves: list[str], index: int) -> dict[str, Any]:
    """Generate a Merkle inclusion proof for ``leaves[index]`` using the same
    Bitcoin-style odd-node duplication that ``verifier.build_root`` uses."""
    import hashlib
    siblings: list[dict[str, str]] = []
    level: list[bytes] = [bytes.fromhex(h) for h in leaves]
    pos = index

    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        sib_idx = pos ^ 1
        side = "L" if sib_idx < pos else "R"
        siblings.append({"side": side, "hash": level[sib_idx].hex()})
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(hashlib.sha256(level[i] + level[i + 1]).digest())
        level = nxt
        pos //= 2

    return {
        "leaf":     leaves[index],
        "index":    index,
        "siblings": siblings,
        "root":     level[0].hex(),
        "size":     len(leaves),
    }


def _build_root(leaves: list[str]) -> str:
    import hashlib
    if not leaves:
        return hashlib.sha256(b"").hexdigest()
    level: list[bytes] = [bytes.fromhex(h) for h in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        nxt = []
        for i in range(0, len(level), 2):
            nxt.append(hashlib.sha256(level[i] + level[i + 1]).digest())
        level = nxt
    return level[0].hex()


def _write_bundle(
    out: Path,
    priv: ed25519.Ed25519PrivateKey,
    pub_pem: str,
    receipts: list[dict[str, Any]],
    leaves: list[str],
    root_payload: dict[str, Any],
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "keys").mkdir(exist_ok=True)
    (out / "keys" / "active.pem").write_text(pub_pem)
    (out / "receipts").mkdir(exist_ok=True)
    (out / "proofs").mkdir(exist_ok=True)
    (out / "roots").mkdir(exist_ok=True)

    for i, signed in enumerate(receipts):
        exec_id = signed["receipt"]["execution_id"]
        (out / "receipts" / f"{exec_id}.json").write_text(json.dumps(signed))
        proof = _build_inclusion_proof(leaves, i)
        (out / "proofs" / f"{exec_id}.json").write_text(
            json.dumps({"proof": proof, "signed_root": root_payload})
        )

    root_date = root_payload["receipt"]["root_date"]
    (out / "roots" / f"{root_date}.json").write_text(json.dumps(root_payload))


@pytest.fixture
def export_bundle(tmp_path: Path) -> tuple[Path, ed25519.Ed25519PrivateKey, str]:
    """Build a 4-row, single-shard export bundle that the verifier accepts."""
    priv, pub_pem = _make_keypair()
    fp = _fingerprint(pub_pem)

    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    base_ts = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)

    rows: list[dict[str, Any]] = []
    prev = GENESIS_HASH
    for i in range(4):
        req_id = f"req-{i:03d}"
        eh = compute_event_hash(
            prev_hash=prev,
            tenant_id=tenant_id,
            agent_id=agent_id,
            action="execute_tool",
            tool="db.query",
            decision="allow",
            request_id=req_id,
        )
        receipt = _build_receipt(
            execution_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            agent_id=agent_id,
            action="execute_tool",
            tool="db.query",
            decision="allow",
            request_id=req_id,
            timestamp=(base_ts + timedelta(seconds=i)).isoformat(),
            event_hash=eh,
            prev_hash=prev,
            chain_shard=0,
        )
        rows.append(_sign(receipt, priv, fp))
        prev = eh

    leaves = [leaf_hash(r) for r in rows]
    root_hash = _build_root(leaves)
    root_date = base_ts.date().isoformat()

    root_receipt = {
        "version":        1,
        "tenant_id":      tenant_id,
        "root_date":      root_date,
        "root_hash":      root_hash,
        "prev_root_hash": None,
        "leaf_count":     len(leaves),
        # Sprint 1.2 will add window_end; for now the full-day root anchors
        # the entire 24h window, so tail anchoring passes vacuously.
        "window_end":     (base_ts + timedelta(days=1)).isoformat(),
    }
    sig = priv.sign(_canonical_json(root_receipt))
    root_payload = {
        "receipt":                root_receipt,
        "signature":              _b64(sig),
        "algorithm":              "ed25519",
        "public_key_fingerprint": fp,
    }

    bundle = tmp_path / "bundle"
    _write_bundle(bundle, priv, pub_pem, rows, leaves, root_payload)
    return bundle, priv, pub_pem


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_bundle_verifies(export_bundle):
    bundle, _, _ = export_bundle
    verifier = AuditVerifier.from_export_dir(bundle)
    result = verifier.verify_export(bundle)

    assert result.ok, f"clean bundle should verify; errors={result.errors}"
    assert result.valid_receipts == result.total_receipts == 4
    assert result.valid_inclusions == result.total_inclusions == 4
    assert result.chain_ok
    assert result.shard_chains_ok
    assert result.truncation_ok


# ---------------------------------------------------------------------------
# Mutation 1: flip a byte in event_hash on one receipt
# ---------------------------------------------------------------------------


def test_flipped_event_hash_is_detected(export_bundle):
    """An attacker who edits event_hash without re-signing — the signature
    verification fails AND the recomputation flags the row independently."""
    bundle, _, _ = export_bundle
    receipts = sorted((bundle / "receipts").glob("*.json"))
    target = receipts[1]
    payload = json.loads(target.read_text())
    # Flip the first hex char of event_hash so it becomes a different valid hex.
    eh = payload["receipt"]["event_hash"]
    payload["receipt"]["event_hash"] = ("f" if eh[0] != "f" else "0") + eh[1:]
    target.write_text(json.dumps(payload))

    verifier = AuditVerifier.from_export_dir(bundle)
    result = verifier.verify_export(bundle)

    assert not result.ok
    # Signature verification must fail because the canonical JSON changed.
    assert result.valid_receipts < result.total_receipts
    # Shard-chain verification must independently flag the mismatch.
    assert not result.shard_chains_ok
    assert any("event_hash mismatch" in m for m in result.shard_chain_errors)


# ---------------------------------------------------------------------------
# Mutation 2: rewrite event_hash AND re-sign (attacker controls the key)
# ---------------------------------------------------------------------------


def test_resigned_tamper_caught_by_shard_chain(export_bundle):
    """The hardest case: attacker controls the signing key, edits a receipt,
    and re-signs. Signature passes — but recompute_event_hash sees that the
    claimed event_hash no longer matches the business fields. Also the next
    row's prev_hash no longer matches."""
    bundle, priv, pub_pem = export_bundle
    fp = _fingerprint(pub_pem)
    receipts = sorted((bundle / "receipts").glob("*.json"))
    target = receipts[1]
    payload = json.loads(target.read_text())

    # Mutate the business content (change decision to "deny") but DON'T
    # recompute the hash — then re-sign over the new content.
    receipt = payload["receipt"]
    receipt["decision"] = "deny"
    new_sig = priv.sign(_canonical_json(receipt))
    payload["receipt"] = receipt
    payload["signature"] = _b64(new_sig)
    payload["public_key_fingerprint"] = fp
    target.write_text(json.dumps(payload))

    verifier = AuditVerifier.from_export_dir(bundle)
    result = verifier.verify_export(bundle)

    # Signature now verifies (attacker has the key).
    assert result.valid_receipts == result.total_receipts
    # But the shard-chain walk independently catches the tamper.
    assert not result.shard_chains_ok
    assert not result.ok
    assert any("event_hash mismatch" in m for m in result.shard_chain_errors)


# ---------------------------------------------------------------------------
# Mutation 3: delete a row (truncation in the middle of the shard)
# ---------------------------------------------------------------------------


def test_deleted_row_breaks_prev_hash_chain(export_bundle):
    bundle, _, _ = export_bundle
    # Find the receipt whose timestamp puts it in the middle of the chain
    # (deleting either endpoint would not break linkage; deleting the middle
    # leaves a row whose prev_hash no longer matches the prior survivor).
    receipts = list((bundle / "receipts").glob("*.json"))
    by_ts = sorted(
        receipts,
        key=lambda p: json.loads(p.read_text())["receipt"]["timestamp"],
    )
    middle = by_ts[1]
    middle.unlink()
    proof_path = bundle / "proofs" / middle.name
    if proof_path.exists():
        proof_path.unlink()

    verifier = AuditVerifier.from_export_dir(bundle)
    result = verifier.verify_export(bundle)

    # The next row's prev_hash now points at a hash for which there is no
    # receipt — shard-chain walk catches the gap.
    assert not result.shard_chains_ok
    assert any("prev_hash break" in m for m in result.shard_chain_errors)


# ---------------------------------------------------------------------------
# Mutation 4: swap the signing key (attacker substitutes their own)
# ---------------------------------------------------------------------------


def test_swapped_signing_key_is_rejected(export_bundle):
    """An attacker re-signs receipts with their own key. The verifier knows
    only the legitimate public key; the fingerprint check fails and the
    signature path returns False."""
    bundle, _, _ = export_bundle
    attacker_priv, attacker_pem = _make_keypair()
    attacker_fp = _fingerprint(attacker_pem)

    # Re-sign the second receipt with the attacker's key.
    receipts = sorted((bundle / "receipts").glob("*.json"))
    target = receipts[1]
    payload = json.loads(target.read_text())
    receipt = payload["receipt"]
    new_sig = attacker_priv.sign(_canonical_json(receipt))
    payload["signature"] = _b64(new_sig)
    payload["public_key_fingerprint"] = attacker_fp
    target.write_text(json.dumps(payload))

    verifier = AuditVerifier.from_export_dir(bundle)
    result = verifier.verify_export(bundle)

    assert not result.ok
    assert result.valid_receipts < result.total_receipts
    # Either "signature INVALID" or "no public keys loaded" — both mean the
    # attacker key was rejected.
    assert any("INVALID" in e or "no public" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Mutation 5: truncate the tail (delete the most recent row)
# ---------------------------------------------------------------------------


def test_truncated_tail_detected_when_unanchored():
    """A tail row whose timestamp is past the most recent signed root anchor
    has no cryptographic anchor and must be reported as an unanchored tail.
    This is the case Sprint 1.2 closes with interim roots — for now we just
    prove the detector fires."""
    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    base = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)

    receipts = []
    prev = GENESIS_HASH
    for i in range(3):
        eh = compute_event_hash(
            prev_hash=prev,
            tenant_id=tenant_id, agent_id=agent_id,
            action="execute_tool", tool="db.query",
            decision="allow", request_id=f"r{i}",
        )
        receipts.append({"receipt": _build_receipt(
            execution_id=str(uuid.uuid4()),
            tenant_id=tenant_id, agent_id=agent_id,
            action="execute_tool", tool="db.query", decision="allow",
            request_id=f"r{i}",
            timestamp=(base + timedelta(minutes=i)).isoformat(),
            event_hash=eh, prev_hash=prev, chain_shard=0,
        )})
        prev = eh

    # Signed root anchors only the first 30 seconds.
    signed_root = {
        "receipt": {
            "tenant_id":  tenant_id,
            "root_date":  base.date().isoformat(),
            "window_end": (base + timedelta(seconds=30)).isoformat(),
        },
    }

    ok, warnings = detect_truncation(receipts, [signed_root])
    assert not ok
    assert any("not yet anchored" in w for w in warnings)


# ---------------------------------------------------------------------------
# Direct shard-chain walker — corner cases
# ---------------------------------------------------------------------------


def test_genesis_row_with_wrong_prev_hash_is_flagged():
    """The first row in a shard must carry prev_hash=GENESIS_HASH."""
    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    bad_prev = "f" * 64
    eh = compute_event_hash(
        prev_hash=bad_prev, tenant_id=tenant_id, agent_id=agent_id,
        action="execute_tool", tool=None, decision="allow", request_id="r0",
    )
    payload = {"receipt": _build_receipt(
        execution_id=str(uuid.uuid4()),
        tenant_id=tenant_id, agent_id=agent_id,
        action="execute_tool", tool=None, decision="allow",
        request_id="r0",
        timestamp="2026-06-12T09:00:00+00:00",
        event_hash=eh, prev_hash=bad_prev, chain_shard=0,
    )}
    ok, errors = verify_shard_chains([payload])
    assert not ok
    assert any("prev_hash break" in e for e in errors)


def test_advancing_window_end_anchors_previously_unanchored_tail():
    """Sprint 1.2 contract: when the scheduler rolls today's running root
    forward, receipts that were past the old window_end become anchored
    against the new one — sub-minute detection AND sub-minute coverage."""
    tenant_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())
    base = datetime(2026, 6, 12, 9, 0, 0, tzinfo=UTC)

    receipts = []
    prev = GENESIS_HASH
    for i in range(3):
        eh = compute_event_hash(
            prev_hash=prev,
            tenant_id=tenant_id, agent_id=agent_id,
            action="execute_tool", tool=None, decision="allow",
            request_id=f"r{i}",
        )
        receipts.append({"receipt": _build_receipt(
            execution_id=str(uuid.uuid4()),
            tenant_id=tenant_id, agent_id=agent_id,
            action="execute_tool", tool=None, decision="allow",
            request_id=f"r{i}",
            timestamp=(base + timedelta(seconds=i * 10)).isoformat(),
            event_hash=eh, prev_hash=prev, chain_shard=0,
        )})
        prev = eh

    # Old interim root anchors only the first 5 seconds.
    old_root = {"receipt": {
        "tenant_id":  tenant_id,
        "root_date":  base.date().isoformat(),
        "window_end": (base + timedelta(seconds=5)).isoformat(),
    }}
    ok_before, warnings_before = detect_truncation(receipts, [old_root])
    assert not ok_before
    assert any("not yet anchored" in w for w in warnings_before)

    # Scheduler rolls forward — new interim root anchors past the last receipt.
    new_root = {"receipt": {
        "tenant_id":  tenant_id,
        "root_date":  base.date().isoformat(),
        "window_end": (base + timedelta(seconds=60)).isoformat(),
    }}
    ok_after, warnings_after = detect_truncation(receipts, [old_root, new_root])
    assert ok_after, f"new root should anchor every receipt; got {warnings_after}"


def test_recompute_event_hash_matches_writer_for_nones():
    """Locks in the wire contract: tool=None and request_id=None must hash
    identically to the writer's stable_json form ('' fallbacks)."""
    h_via_helper = recompute_event_hash({
        "prev_hash":  GENESIS_HASH,
        "tenant_id":  "t",
        "agent_id":   "a",
        "action":     "x",
        "tool":       None,
        "decision":   "allow",
        "request_id": None,
    })
    h_direct = compute_event_hash(
        prev_hash=GENESIS_HASH, tenant_id="t", agent_id="a",
        action="x", tool=None, decision="allow", request_id=None,
    )
    assert h_via_helper == h_direct
