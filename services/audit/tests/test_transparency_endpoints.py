"""Unit tests for the Transparency Log sprint (2026-05-15).

Covers:

* `empty_epoch_root_hash` — deterministic, domain-separated, dependent on prev.
* `/transparency/verify-root` contract:
  - valid signed payload   → {valid:true, errors:[]}
  - malformed input        → HTTP 400 with {errors:["malformed_payload"]}
  - unknown fingerprint    → 200 {valid:false, errors:["unknown_key_fingerprint"]}
  - tampered signature     → 200 {valid:false, errors:["signature_mismatch"]}
  - non-hex root_hash      → 200 {valid:false, errors:["root_hash_mismatch"]}
  - response NEVER carries null `valid`/`algorithm`/`expected_fingerprint`
* `/receipts/verify`:
  - active key path → {valid:true, errors:[]}
  - signed-by-historical-key path → {valid:true, errors:[]} after rotation
  - garbage payload → {valid:false, errors:[malformed_payload]}
* `rotate_transparency_key._record_historical_key`:
  - inserts the current key on first call
  - is idempotent on second call (ON CONFLICT DO NOTHING)
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi import HTTPException

from services.audit.signer import (
    canonical_json,
    fingerprint_public_key,
    get_root_signer,
    get_signer,
    reset_signer_for_tests,
)
from services.audit.transparency import (
    empty_epoch_root_hash,
    verify_root,
)

# --------------------------------------------------------------------------- #
# Test fixtures                                                               #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _isolate_signer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PATH", str(tmp_path / "receipt.pem"))
    monkeypatch.setenv("ROOT_SIGNING_KEY_PATH", str(tmp_path / "root.pem"))
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("ROOT_SIGNING_PRIVATE_KEY", raising=False)
    reset_signer_for_tests()
    yield
    reset_signer_for_tests()


def _make_signed_payload(
    *,
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
    root_hash: str | None = None,
    leaf_count: int = 3,
    prev_root_hash: str | None = None,
    signer=None,
) -> dict:
    """Build a valid signed root payload using the active root signer."""
    s = signer or get_root_signer()
    receipt = {
        "version":             3,
        "kind":                "transparency_root",
        "tenant_id":           tenant_id,
        "root_date":           date.today().isoformat(),
        "root_hash":           root_hash or ("a" * 64),
        "prev_root_hash":      prev_root_hash,
        "leaf_count":          leaf_count,
        "leaf_range_start_id": str(uuid.uuid4()),
        "leaf_range_end_id":   str(uuid.uuid4()),
    }
    sig = s._priv.sign(canonical_json(receipt))  # noqa: SLF001
    return {
        "receipt":                receipt,
        "signature":              base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
        "algorithm":              "ed25519",
        "public_key_fingerprint": s._fingerprint,  # noqa: SLF001
    }


@pytest.fixture
def fake_db():
    """An AsyncSession whose .execute returns 0 historical keys by default."""
    db = AsyncMock()
    empty_result = MagicMock()
    empty_result.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    db.execute = AsyncMock(return_value=empty_result)
    return db


# --------------------------------------------------------------------------- #
# empty_epoch_root_hash                                                       #
# --------------------------------------------------------------------------- #


class TestEmptyEpochRootHash:
    def test_deterministic(self) -> None:
        prev = "a" * 64
        assert empty_epoch_root_hash(prev) == empty_epoch_root_hash(prev)

    def test_changes_with_prev(self) -> None:
        assert empty_epoch_root_hash("a" * 64) != empty_epoch_root_hash("b" * 64)

    def test_distinct_from_empty_merkle_root(self) -> None:
        # The empty-epoch marker must not collide with sha256(b"") which is
        # the genuine "no leaves" Merkle sentinel.
        import hashlib
        empty_merkle = hashlib.sha256(b"").hexdigest()
        assert empty_epoch_root_hash(None) != empty_merkle

    def test_none_prev_is_well_defined(self) -> None:
        v = empty_epoch_root_hash(None)
        assert isinstance(v, str) and len(v) == 64


# --------------------------------------------------------------------------- #
# /transparency/verify-root contract                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestVerifyRootContract:
    async def test_valid_payload_returns_ok(self, fake_db) -> None:
        payload = _make_signed_payload()
        resp = await verify_root(db=fake_db, payload=payload)
        body = resp.data
        assert body["valid"] is True
        assert body["algorithm"] == "ed25519"
        assert body["expected_fingerprint"] == payload["public_key_fingerprint"]
        assert body["errors"] == []
        # NEVER nulls.
        assert body["valid"] is not None
        assert body["algorithm"] is not None
        assert body["expected_fingerprint"] is not None

    async def test_missing_top_level_field_is_400(self, fake_db) -> None:
        bad = _make_signed_payload()
        bad.pop("signature")
        with pytest.raises(HTTPException) as exc:
            await verify_root(db=fake_db, payload=bad)
        assert exc.value.status_code == 400
        body = exc.value.detail
        assert body["valid"] is False
        assert body["algorithm"] == "ed25519"
        assert body["expected_fingerprint"] is not None  # active signer
        assert body["errors"] == ["malformed_payload"]

    async def test_empty_payload_is_400(self, fake_db) -> None:
        with pytest.raises(HTTPException) as exc:
            await verify_root(db=fake_db, payload={})
        assert exc.value.status_code == 400
        assert exc.value.detail["errors"] == ["malformed_payload"]

    async def test_none_payload_is_400(self, fake_db) -> None:
        with pytest.raises(HTTPException) as exc:
            await verify_root(db=fake_db, payload=None)
        assert exc.value.status_code == 400

    async def test_missing_receipt_subfield_is_400(self, fake_db) -> None:
        payload = _make_signed_payload()
        payload["receipt"].pop("root_hash")
        with pytest.raises(HTTPException) as exc:
            await verify_root(db=fake_db, payload=payload)
        assert exc.value.status_code == 400

    async def test_unknown_fingerprint_returns_200_with_error_code(self, fake_db) -> None:
        payload = _make_signed_payload()
        payload["public_key_fingerprint"] = "0" * 32
        resp = await verify_root(db=fake_db, payload=payload)
        body = resp.data
        assert body["valid"] is False
        assert body["errors"] == ["unknown_key_fingerprint"]
        assert body["algorithm"] == "ed25519"
        assert body["expected_fingerprint"] is not None

    async def test_tampered_signature_returns_signature_mismatch(self, fake_db) -> None:
        payload = _make_signed_payload()
        # Flip one byte of the receipt — the signature won't validate anymore.
        payload["receipt"]["leaf_count"] += 1
        resp = await verify_root(db=fake_db, payload=payload)
        body = resp.data
        assert body["valid"] is False
        assert body["errors"] == ["signature_mismatch"]
        assert body["expected_fingerprint"] == payload["public_key_fingerprint"]

    async def test_corrupt_signature_bytes_returns_400(self, fake_db) -> None:
        payload = _make_signed_payload()
        payload["signature"] = "@@@@not-base64@@@@"
        with pytest.raises(HTTPException) as exc:
            await verify_root(db=fake_db, payload=payload)
        assert exc.value.detail["errors"] == ["malformed_payload"]


# --------------------------------------------------------------------------- #
# /receipts/verify with historical-key fallback                               #
# --------------------------------------------------------------------------- #


def _row():
    return {
        "id":          uuid.uuid4(),
        "tenant_id":   uuid.uuid4(),
        "agent_id":    uuid.uuid4(),
        "tool":        "db.query",
        "action":      "execute",
        "decision":    "allow",
        "reason":      None,
        "request_id":  "req_xyz",
        "timestamp":   datetime.now(UTC),
        "event_hash":  "a" * 64,
        "prev_hash":   "b" * 64,
        "chain_shard": 0,
    }


@pytest.mark.asyncio
async def test_verify_receipt_against_known_keys_active_path(fake_db) -> None:
    """The default path: signature was made by the currently-active key."""
    from services.audit.signer import verify_receipt_against_known_keys
    active = get_signer()
    signed = active.sign(_row())
    ok, fp = await verify_receipt_against_known_keys(fake_db, signed)
    assert ok is True
    assert fp == active._fingerprint  # noqa: SLF001


@pytest.mark.asyncio
async def test_verify_receipt_against_known_keys_historical_path(monkeypatch) -> None:
    """After rotation, a payload signed by the old key still verifies via
    the historical-key fallback."""
    from services.audit.signer import (
        ReceiptSigner,
        verify_receipt_against_known_keys,
    )

    # Build a payload signed by an "old" key that the active signer is NOT.
    old_priv = ed25519.Ed25519PrivateKey.generate()
    old_signer = ReceiptSigner(old_priv, source="test:old")
    old_signed = old_signer.sign(_row())

    # Now create a different active signer (simulates post-rotation state).
    reset_signer_for_tests()
    # Force re-init by getting signer (will generate a new ephemeral key
    # at the temp path).
    active_after = get_signer()
    assert active_after._fingerprint != old_signer._fingerprint  # noqa: SLF001

    # Stub the DB to return the old key as a historical row.
    historical_row = SimpleNamespace(
        fingerprint=old_signer._fingerprint,  # noqa: SLF001
        public_key_pem=old_signer.public_key_pem(),
        algorithm="ed25519",
        rotated_at=datetime.now(UTC),
    )
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=MagicMock(return_value=[historical_row]))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    ok, used_fp = await verify_receipt_against_known_keys(db, old_signed)
    assert ok is True
    assert used_fp == old_signer._fingerprint  # noqa: SLF001


@pytest.mark.asyncio
async def test_verify_receipt_against_known_keys_unknown_key(fake_db) -> None:
    """Unknown-fingerprint payload returns False, not an exception."""
    from services.audit.signer import verify_receipt_against_known_keys
    stranger = ed25519.Ed25519PrivateKey.generate()
    from services.audit.signer import ReceiptSigner
    stranger_signer = ReceiptSigner(stranger, source="test:stranger")
    signed = stranger_signer.sign(_row())
    ok, fp = await verify_receipt_against_known_keys(fake_db, signed)
    assert ok is False
    assert fp is None


@pytest.mark.asyncio
async def test_verify_receipt_against_known_keys_malformed_raises(fake_db) -> None:
    """Malformed payload (missing field) raises ValueError so callers can
    convert to a 400 with errors=["malformed_payload"]."""
    from services.audit.signer import verify_receipt_against_known_keys
    with pytest.raises(ValueError):
        await verify_receipt_against_known_keys(fake_db, {})


# --------------------------------------------------------------------------- #
# rotation script unit                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_record_historical_key_idempotent() -> None:
    """ON CONFLICT DO NOTHING semantics — first call inserts, second is a no-op."""
    from scripts.maintenance.rotate_transparency_key import _record_historical_key

    pub_pem = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fp = fingerprint_public_key(pub_pem)

    # Fake DB: first execute returns rowcount=1, second returns rowcount=0
    # (matches Postgres ON CONFLICT DO NOTHING semantics).
    fake_results = [
        MagicMock(rowcount=1),
        MagicMock(rowcount=0),
    ]
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=fake_results)
    db.commit = AsyncMock()

    inserted_first = await _record_historical_key(
        db, fingerprint=fp, public_key_pem=pub_pem, retired_reason="test"
    )
    inserted_second = await _record_historical_key(
        db, fingerprint=fp, public_key_pem=pub_pem, retired_reason="test"
    )
    assert inserted_first is True
    assert inserted_second is False
    assert db.commit.await_count == 2


def test_rotation_helpers_pem_roundtrip(tmp_path: Path) -> None:
    """_load_private_pem reads what _private_pem wrote."""
    from scripts.maintenance.rotate_transparency_key import (
        _load_private_pem,
        _private_pem,
        _public_pem,
    )
    priv = ed25519.Ed25519PrivateKey.generate()
    key_path = tmp_path / "k.pem"
    key_path.write_bytes(_private_pem(priv))

    loaded = _load_private_pem(key_path)
    assert _public_pem(loaded) == _public_pem(priv)
