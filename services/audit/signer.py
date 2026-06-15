"""Cryptographic execution receipts.

For every audit row, ACP produces a structured receipt and signs it with an
ed25519 key. Customers (or auditors) can fetch the receipt + the public key
and verify offline — no trust in ACP's word required.

Receipt format (canonical JSON, keys sorted, no whitespace):

    {
      "version":      1,
      "execution_id": "<audit row id>",
      "tenant_id":    "<uuid>",
      "agent_id":     "<uuid>",
      "tool":         "<string|null>",
      "action":       "<string>",
      "decision":     "allow|deny|error",
      "reason":       "<string|null>",
      "request_id":   "<string|null>",
      "timestamp":    "<ISO-8601>",
      "event_hash":   "<hex64|null>",
      "prev_hash":    "<hex64|null>",
      "chain_shard":  "<int>"
    }

Signature: ed25519 over the UTF-8 bytes of the canonical JSON. Base64
(url-safe, no padding) encoded.

Key storage precedence:

    1. RECEIPT_SIGNING_PRIVATE_KEY env var (base64 PEM) — preferred for prod
       (mount the secret, don't put a key on disk).
    2. /data/keys/receipt-signing.pem on the audit container (persistent
       volume — survives restarts).
    3. Generate fresh in-memory and warn — only acceptable in tests.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog  # noqa: F401
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

log = structlog.get_logger(__name__)

_DEFAULT_KEY_PATH = Path("/data/keys/receipt-signing.pem")
_DEFAULT_ROOT_KEY_PATH = Path("/data/keys/root-signing.pem")
_ALGORITHM = "ed25519"
_RECEIPT_VERSION = 1

_lock = threading.RLock()  # RLock: get_root_signer() holds lock while calling get_signer()
_signer: ReceiptSigner | None = None
_root_signer: ReceiptSigner | None = None


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def canonical_json(obj: dict[str, Any]) -> bytes:
    """Stable canonical JSON: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def fingerprint_public_key(pub_pem: bytes) -> str:
    """Public-key fingerprint = first 16 bytes of sha256(PEM), hex."""
    return hashlib.sha256(pub_pem).hexdigest()[:32]


class ReceiptSigner:
    def __init__(self, private_key: ed25519.Ed25519PrivateKey, source: str) -> None:
        self._priv = private_key
        self._pub = private_key.public_key()
        self._pub_pem = self._pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self._fingerprint = fingerprint_public_key(self._pub_pem)
        self._created_at = datetime.now(UTC).isoformat()
        log.info(
            "receipt_signer_ready",
            source=source,
            fingerprint=self._fingerprint,
            algorithm=_ALGORITHM,
        )

    # ── Public key surface ────────────────────────────────────────────────
    def public_key_pem(self) -> str:
        return self._pub_pem.decode("ascii")

    def public_key_info(self) -> dict[str, Any]:
        return {
            "algorithm": _ALGORITHM,
            "public_key_pem": self.public_key_pem(),
            "fingerprint": self._fingerprint,
            "public_key_fingerprint": self._fingerprint,
            "created_at": self._created_at,
        }

    # ── Build + sign ──────────────────────────────────────────────────────
    def build_receipt(self, row: Any) -> dict[str, Any]:
        """Build the canonical receipt dict for an AuditLog row.

        Accepts either a SQLAlchemy AuditLog instance or a dict-like with the
        same attributes/keys. Coerces UUIDs and datetimes to strings.
        """
        getter = (lambda k: row.get(k)) if isinstance(row, dict) else (lambda k: getattr(row, k, None))

        ts = getter("timestamp")
        if hasattr(ts, "isoformat"):
            ts = ts.isoformat()

        return {
            "version":      _RECEIPT_VERSION,
            "execution_id": _stringify(getter("id")),
            "tenant_id":    _stringify(getter("tenant_id")),
            "agent_id":     _stringify(getter("agent_id")),
            "tool":         getter("tool"),
            "action":       getter("action"),
            "decision":     getter("decision"),
            "reason":       getter("reason"),
            "request_id":   getter("request_id"),
            "timestamp":    ts,
            "event_hash":   getter("event_hash"),
            "prev_hash":    getter("prev_hash"),
            "chain_shard":  int(getter("chain_shard") or 0),
        }

    def sign(self, row: Any) -> dict[str, Any]:
        """Return the full signed-receipt payload for an audit row."""
        receipt = self.build_receipt(row)
        sig = self._priv.sign(canonical_json(receipt))
        return {
            "receipt":                receipt,
            "signature":              _b64(sig),
            "algorithm":              _ALGORITHM,
            "public_key_fingerprint": self._fingerprint,
        }


# ── Verifier (also used by the SDK; pure-function so it has no state) ───
def verify_receipt(payload: dict[str, Any], public_key_pem: str) -> bool:
    """Verify a signed-receipt payload against a known public key.

    Returns True iff the signature, fingerprint, and canonical encoding all
    agree. Raises ValueError on missing fields so callers can distinguish
    "bad payload" from "valid payload that didn't verify."
    """
    for k in ("receipt", "signature", "algorithm", "public_key_fingerprint"):
        if k not in payload:
            raise ValueError(f"missing field: {k}")
    if payload["algorithm"] != _ALGORITHM:
        raise ValueError(f"unsupported algorithm: {payload['algorithm']}")

    pub_pem = public_key_pem.encode("ascii")
    if fingerprint_public_key(pub_pem) != payload["public_key_fingerprint"]:
        return False

    try:
        pub = serialization.load_pem_public_key(pub_pem)
    except ValueError as e:
        raise ValueError(f"invalid public key PEM: {e}") from e
    if not isinstance(pub, ed25519.Ed25519PublicKey):
        raise ValueError("public key is not ed25519")

    try:
        pub.verify(_b64d(payload["signature"]), canonical_json(payload["receipt"]))
        return True
    except Exception:
        return False


# ── Singleton bootstrap ─────────────────────────────────────────────────
def _load_from_env() -> ed25519.Ed25519PrivateKey | None:
    raw = os.environ.get("RECEIPT_SIGNING_PRIVATE_KEY")
    if not raw:
        return None
    try:
        pem = base64.b64decode(raw)
    except Exception:
        pem = raw.encode("ascii")
    return _load_pem_private(pem)


def _load_pem_private(pem: bytes) -> ed25519.Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("RECEIPT_SIGNING_PRIVATE_KEY must be ed25519")
    return key


def _load_from_disk(path: Path) -> ed25519.Ed25519PrivateKey | None:
    if not path.exists():
        return None
    return _load_pem_private(path.read_bytes())


def _generate_and_persist(path: Path) -> tuple[ed25519.Ed25519PrivateKey, str]:
    priv = ed25519.Ed25519PrivateKey.generate()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            priv.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        with contextlib.suppress(OSError):
            path.chmod(0o600)
        return priv, f"generated:{path}"
    except OSError:
        log.warning("receipt_signer_ephemeral", reason="cannot_persist", path=str(path))
        return priv, "generated:ephemeral"


def get_signer() -> ReceiptSigner:
    """The ed25519 key used to sign individual execution receipts.

    Sprint 1.3: key custody routes through
    :class:`sdk.common.signing_keys.SigningKeyProvider`. By default the
    provider is :class:`LocalFileSigningKeyProvider` (the previous behavior).
    Set ``RECEIPT_SIGNING_PROVIDER=kms`` to switch to KMS envelope encryption
    so the plaintext PEM never touches disk.
    """
    global _signer
    if _signer is not None:
        return _signer
    with _lock:
        if _signer is not None:
            return _signer

        from sdk.common.signing_keys import provider_from_env  # noqa: PLC0415

        key_path = Path(os.environ.get("RECEIPT_SIGNING_KEY_PATH") or _DEFAULT_KEY_PATH)
        provider = provider_from_env(
            provider_env="RECEIPT_SIGNING_PROVIDER",
            pem_env="RECEIPT_SIGNING_PRIVATE_KEY",
            disk_path=key_path,
            kms_key_id_env="RECEIPT_SIGNING_KMS_KEY_ID",
            kms_blob_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_B64",
            kms_s3_uri_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI",
            ssm_parameter_env="RECEIPT_SIGNING_SSM_PARAMETER",
        )
        priv, source = provider.load()
        _signer = ReceiptSigner(priv, source=source)
        return _signer


def get_root_signer() -> ReceiptSigner:
    """The ed25519 key used to sign daily transparency roots.

    Separated from `get_signer()` so the receipt-signing key can be rotated
    independently — old daily roots remain verifiable against their original
    root-signing key even after the receipt key rotates.

    Falls back to the receipt-signing key when `ROOT_SIGNING_KEY_PATH` is
    unset (back-compat with deployments that pre-date key separation).
    """
    global _root_signer
    if _root_signer is not None:
        return _root_signer
    with _lock:
        if _root_signer is not None:
            return _root_signer

        from sdk.common.signing_keys import provider_from_env  # noqa: PLC0415

        # The root key is independently configurable. If nothing's set we fall
        # back to the receipt signer for back-compat with single-key deployments.
        provider_choice = os.environ.get("ROOT_SIGNING_PROVIDER", "").strip().lower()
        env_path = os.environ.get("ROOT_SIGNING_KEY_PATH")
        env_pem = os.environ.get("ROOT_SIGNING_PRIVATE_KEY")
        env_kms_key = os.environ.get("ROOT_SIGNING_KMS_KEY_ID")
        if not provider_choice and not env_path and not env_pem and not env_kms_key:
            _root_signer = get_signer()
            log.info("root_signer_reuses_receipt_key", reason="no root-key config set")
            return _root_signer

        key_path = Path(env_path) if env_path else _DEFAULT_ROOT_KEY_PATH
        provider = provider_from_env(
            provider_env="ROOT_SIGNING_PROVIDER",
            pem_env="ROOT_SIGNING_PRIVATE_KEY",
            disk_path=key_path,
            kms_key_id_env="ROOT_SIGNING_KMS_KEY_ID",
            kms_blob_env="ROOT_SIGNING_KMS_CIPHERTEXT_B64",
            kms_s3_uri_env="ROOT_SIGNING_KMS_CIPHERTEXT_S3_URI",
            ssm_parameter_env="ROOT_SIGNING_SSM_PARAMETER",
        )
        priv, source = provider.load()
        _root_signer = ReceiptSigner(priv, source=f"root:{source}")
        return _root_signer


def reset_signer_for_tests() -> None:
    """Drop the cached singletons — only for unit tests."""
    global _signer, _root_signer
    with _lock:
        _signer = None
        _root_signer = None


# ── Historical key registry (DB-backed) ──────────────────────────────────
async def load_historical_public_keys(db: Any) -> list[dict[str, str]]:
    """Read the rotated-key registry from `transparency_historical_keys`.

    Returns a list of `{fingerprint, public_key_pem, algorithm, rotated_at}`
    dicts sorted by rotated_at desc. Used by `/receipts/verify` +
    `/transparency/verify-root` to authenticate payloads signed by a
    previously-active key after rotation.

    Lazy import of the model so this module can still be imported in
    test contexts where the audit DB isn't initialised.
    """
    from sqlalchemy import select  # local import — see module note

    from services.audit.models import TransparencyHistoricalKey  # noqa: WPS433

    rows = (
        await db.execute(
            select(TransparencyHistoricalKey).order_by(
                TransparencyHistoricalKey.rotated_at.desc()
            )
        )
    ).scalars().all()
    return [
        {
            "fingerprint":    r.fingerprint,
            "public_key_pem": r.public_key_pem,
            "algorithm":      r.algorithm,
            "rotated_at":     r.rotated_at.isoformat() if r.rotated_at else None,
        }
        for r in rows
    ]


async def verify_receipt_against_known_keys(
    db: Any,
    payload: dict[str, Any],
) -> tuple[bool, str | None]:
    """Try the active receipt-signing key first; on fingerprint mismatch fall
    back to historical keys from the DB. Returns `(valid, used_fingerprint)`.
    Raises ValueError on malformed payload (caller surfaces 400 / "errors").
    """
    # Active path
    active = get_signer()
    try:
        if verify_receipt(payload, active.public_key_pem()):
            return True, active._fingerprint  # noqa: SLF001
    except ValueError:
        raise

    # Fingerprint mismatch on active key — consult history.
    fp = payload.get("public_key_fingerprint")
    for hist in await load_historical_public_keys(db):
        if hist["fingerprint"] != fp:
            continue
        try:
            if verify_receipt(payload, hist["public_key_pem"]):
                return True, hist["fingerprint"]
        except ValueError:
            return False, None
    return False, None


def _stringify(v: Any) -> str | None:
    if v is None:
        return None
    return str(v)
