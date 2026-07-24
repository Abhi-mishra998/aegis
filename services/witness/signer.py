"""Ed25519 signer for Witness attestations.

Key resolution order (first match wins):

  1. ``WITNESS_SIGNING_KEY_PEM`` env var — inline PEM (SSM in prod).
  2. ``WITNESS_SIGNING_KEY_PATH`` env var — file path. Path is
     created on first boot if it doesn't exist AND we're not in
     production (dev/CI persistence).
  3. In production, if neither is set, the service REFUSES to boot.
     An ephemeral key would rotate the attestation fingerprint on
     every restart — every verifier that cached the previous
     fingerprint would fail and cascading auditor rejections would
     follow.
  4. In dev/test, generate an ephemeral key + log a WARNING telling
     the operator to set one of the env vars for stable runs.

Key files are written with mode 0600 (owner-only) and the parent
directory is created with mode 0700.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from services.witness.schemas import Attestation, WitnessVerdict

logger = structlog.get_logger(__name__)


def _canonical_bytes(body: dict) -> bytes:
    """RFC 8785 JCS canonicalization — matches the audit signer."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


class WitnessSigner:
    def __init__(self, witness_id: str, private_key: ed25519.Ed25519PrivateKey | None = None) -> None:
        self.witness_id = witness_id
        self._key = private_key or ed25519.Ed25519PrivateKey.generate()
        pub = self._key.public_key()
        self.public_key_pem = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        self.fingerprint = "sha256:" + hashlib.sha256(
            self.public_key_pem.encode("ascii")
        ).hexdigest()

    def sign(
        self,
        gate_decision_id: str,
        claim: str,
        verdict: WitnessVerdict,
        evidence: list[dict],
    ) -> Attestation:
        body = {
            "attestation_version": "3.0",
            "gate_decision_id": gate_decision_id,
            "claim": claim,
            "verdict": verdict,
            "evidence": evidence,
            "witness_id": self.witness_id,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
        sig = self._key.sign(_canonical_bytes(body))
        return Attestation(**body, signature=_b64url(sig))


def _load_env_key() -> ed25519.Ed25519PrivateKey | None:
    pem = os.getenv("WITNESS_SIGNING_KEY_PEM")
    if not pem:
        return None
    try:
        key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
    except (ValueError, TypeError):
        return None
    return key if isinstance(key, ed25519.Ed25519PrivateKey) else None


def _load_disk_key(path: Path) -> ed25519.Ed25519PrivateKey | None:
    """Load an existing on-disk PEM. Returns None if file missing;
    raises on parse errors so a corrupted key file fails LOUD."""
    if not path.exists():
        return None
    pem = path.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise RuntimeError(f"{path}: not an Ed25519 key")
    return key


def _generate_and_persist(path: Path) -> ed25519.Ed25519PrivateKey:
    """Mint a new Ed25519 key and persist it ATOMICALLY.

    The write happens to a sibling tmp file, `os.fsync`s the file
    descriptor + parent directory, and `os.rename`s over the target.
    On POSIX `rename` is atomic within the same filesystem, so a
    partial-write on disk-full, power loss, or SIGKILL leaves either
    the OLD key file OR nothing — never a corrupted target that
    `_load_disk_key` would fail LOUD on next boot (which would then
    refuse-to-boot in production because the "corrupted PEM" path
    raises).

    File permissions: mode 0600 via `os.open` (open-then-write) BEFORE
    rename, so the file is never observably world-readable. Parent
    directory chmod 0700 is best-effort — some volumes disallow chmod
    on mounted directories under an unprivileged UID.
    """
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        parent.chmod(0o700)
    except OSError:
        pass

    # Use a UNIQUE tmp name so parallel signer inits (should never
    # happen — the caller singletons the signer — but defense in depth)
    # don't race on the same tmp path.
    tmp_path = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        written = 0
        while written < len(pem):
            n = os.write(fd, pem[written:])
            if n <= 0:
                raise OSError(f"partial write to {tmp_path}: {written}/{len(pem)}")
            written += n
        os.fsync(fd)
    finally:
        os.close(fd)

    # Explicit chmod post-write in case umask inserted extra bits on
    # some platforms (macOS in particular).
    tmp_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    # Atomic rename over the target. On POSIX this is a single directory
    # inode update; readers see either the old inode or the new one.
    os.rename(tmp_path, path)

    # Sync the parent directory so the rename survives a crash. This
    # matters for durability more than atomicity — the rename is atomic
    # regardless, but without fsync-parent an OS crash between rename
    # and journal commit could lose the entry entirely.
    try:
        dir_fd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        # Some filesystems (tmpfs, some FUSE mounts) don't support
        # directory fsync. Not fatal; the rename itself is atomic.
        pass

    return key


def _resolve_key() -> ed25519.Ed25519PrivateKey:
    """Resolve the signing key per the documented precedence.

    Fail-CLOSED in production: refuse to boot if neither the env-var
    PEM nor a persistable file path is available. An ephemeral key in
    production silently rotates the attestation fingerprint on every
    restart — verifiers who cached the previous one see valid
    signatures against a key they've never seen and reject.
    """
    env_key = _load_env_key()
    if env_key is not None:
        return env_key

    path_str = os.getenv("WITNESS_SIGNING_KEY_PATH", "")
    if path_str:
        path = Path(path_str)
        existing = _load_disk_key(path)
        if existing is not None:
            return existing
        # Path configured but file missing → mint + persist.
        # Same behavior in dev + prod: the OPERATOR named the path,
        # they intended persistence.
        logger.info("witness_signer_generating_new_persisted_key", path=str(path))
        return _generate_and_persist(path)

    env = (os.getenv("ENVIRONMENT", "development") or "development").lower()
    if env == "production":
        raise RuntimeError(
            "Witness signer key not configured. Set WITNESS_SIGNING_KEY_PEM "
            "or WITNESS_SIGNING_KEY_PATH — refusing to boot with an "
            "ephemeral key in production because every restart would "
            "rotate the attestation fingerprint and break verifiers.",
        )

    # dev/test — ephemeral key, warn loudly.
    logger.warning(
        "witness_signer_ephemeral_key_dev_only",
        env=env,
        note="set WITNESS_SIGNING_KEY_PATH for stable local runs",
    )
    return ed25519.Ed25519PrivateKey.generate()


_singleton: WitnessSigner | None = None


def get_signer() -> WitnessSigner:
    global _singleton
    if _singleton is None:
        witness_id = os.getenv(
            "WITNESS_ID",
            "spiffe://local/witness/dev-0",
        )
        _singleton = WitnessSigner(witness_id, _resolve_key())
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Test-only reset. Real deployments never call this."""
    global _singleton
    _singleton = None
