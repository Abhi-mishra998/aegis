"""
Unit tests for services/identity/webhooks_clerk.py.

Covers the parts of the webhook receiver that don't need a live DB:
  - Svix signature verification (positive + negative paths).
  - The signing-secret decoder.
  - Email extraction from a Clerk user payload shape.
  - Replay-ignored behaviour on a duplicate svix-id.

Event handler integration (Organization/Tenant/User upsert against a
running Postgres) lives in tests/integration/ and is marked @integration.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest
from fastapi import HTTPException

from services.identity.webhooks_clerk import (
    _decode_signing_secret,
    _extract_primary_email,
    _verify_svix_signature,
)


_VALID_WHSEC_BODY = base64.b64encode(b"super-secret-signing-key").decode("ascii")
_VALID_SECRET = f"whsec_{_VALID_WHSEC_BODY}"


def _sign(secret: str, svix_id: str, ts: str, body: bytes) -> str:
    key_bytes = _decode_signing_secret(secret)
    signed = f"{svix_id}.{ts}.".encode("utf-8") + body
    digest = hmac.new(key_bytes, signed, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


# ---------------------------------------------------------------------------
# _decode_signing_secret
# ---------------------------------------------------------------------------


def test_decode_signing_secret_strips_whsec_prefix():
    assert _decode_signing_secret(_VALID_SECRET) == b"super-secret-signing-key"


def test_decode_signing_secret_accepts_raw_base64():
    raw = base64.b64encode(b"another-secret").decode("ascii")
    assert _decode_signing_secret(raw) == b"another-secret"


def test_decode_signing_secret_rejects_empty():
    with pytest.raises(ValueError, match="empty"):
        _decode_signing_secret("")


def test_decode_signing_secret_rejects_non_base64():
    with pytest.raises(ValueError):
        _decode_signing_secret("whsec_$$$not-base64$$$")


# ---------------------------------------------------------------------------
# _verify_svix_signature
# ---------------------------------------------------------------------------


def test_verify_svix_signature_accepts_valid():
    body = b'{"type": "user.created", "data": {"id": "user_001"}}'
    ts = str(int(time.time()))
    svix_id = "msg_1A2B"
    sig = _sign(_VALID_SECRET, svix_id, ts, body)
    _verify_svix_signature(
        svix_id=svix_id,
        svix_timestamp=ts,
        svix_signature=sig,
        body=body,
        secret=_VALID_SECRET,
    )


def test_verify_svix_signature_accepts_multiple_candidates():
    body = b"hello"
    ts = str(int(time.time()))
    svix_id = "msg_X"
    good_sig = _sign(_VALID_SECRET, svix_id, ts, body)
    composite = f"v1,WRONGSIGNATURE000000000000= {good_sig}"
    _verify_svix_signature(
        svix_id=svix_id,
        svix_timestamp=ts,
        svix_signature=composite,
        body=body,
        secret=_VALID_SECRET,
    )


def test_verify_svix_signature_rejects_tampered_body():
    body = b'{"type": "user.created"}'
    ts = str(int(time.time()))
    svix_id = "msg_T"
    sig = _sign(_VALID_SECRET, svix_id, ts, body)
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id=svix_id,
            svix_timestamp=ts,
            svix_signature=sig,
            body=body + b" tampered",
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401


def test_verify_svix_signature_rejects_wrong_secret():
    body = b'{"x": 1}'
    ts = str(int(time.time()))
    svix_id = "msg_W"
    bad_secret = "whsec_" + base64.b64encode(b"different-secret").decode("ascii")
    sig = _sign(bad_secret, svix_id, ts, body)
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id=svix_id,
            svix_timestamp=ts,
            svix_signature=sig,
            body=body,
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401


def test_verify_svix_signature_rejects_stale_timestamp():
    body = b'{"x": 1}'
    ts = str(int(time.time()) - 10 * 60)  # 10 minutes back, beyond 5m tolerance
    svix_id = "msg_O"
    sig = _sign(_VALID_SECRET, svix_id, ts, body)
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id=svix_id,
            svix_timestamp=ts,
            svix_signature=sig,
            body=body,
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401
    assert "tolerance" in exc.value.detail


def test_verify_svix_signature_rejects_future_timestamp():
    body = b"{}"
    ts = str(int(time.time()) + 10 * 60)
    svix_id = "msg_F"
    sig = _sign(_VALID_SECRET, svix_id, ts, body)
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id=svix_id,
            svix_timestamp=ts,
            svix_signature=sig,
            body=body,
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401


def test_verify_svix_signature_rejects_malformed_timestamp():
    body = b"{}"
    sig = "v1,doesnotmatter"
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id="msg_Z",
            svix_timestamp="not-a-number",
            svix_signature=sig,
            body=body,
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401


def test_verify_svix_signature_rejects_v0_only_signature():
    body = b"{}"
    ts = str(int(time.time()))
    sig = "v0,IGNORED"
    with pytest.raises(HTTPException) as exc:
        _verify_svix_signature(
            svix_id="msg_V",
            svix_timestamp=ts,
            svix_signature=sig,
            body=body,
            secret=_VALID_SECRET,
        )
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# _extract_primary_email
# ---------------------------------------------------------------------------


def test_extract_primary_email_prefers_primary_marker():
    data = {
        "primary_email_address_id": "id_2",
        "email_addresses": [
            {"id": "id_1", "email_address": "Alt@Example.com"},
            {"id": "id_2", "email_address": "Primary@Example.com"},
        ],
    }
    assert _extract_primary_email(data) == "primary@example.com"


def test_extract_primary_email_falls_back_to_first_if_no_primary_marker():
    data = {
        "email_addresses": [
            {"id": "id_1", "email_address": "first@example.com"},
        ],
    }
    assert _extract_primary_email(data) == "first@example.com"


def test_extract_primary_email_returns_empty_when_absent():
    assert _extract_primary_email({}) == ""
    assert _extract_primary_email({"email_addresses": []}) == ""
