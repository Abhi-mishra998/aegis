#!/usr/bin/env python3
"""Rotate the transparency-log root-signing key.

Mechanics:

  1. Read the currently-active key from disk (default
     ``/data/keys/root-signing.pem``, override with --key-path).
  2. Compute its fingerprint + PEM.
  3. Generate a new Ed25519 keypair.
  4. **ATF §14.5 cross-signing** — fetch the retiring key's most recent
     TransparencyRoot (if any) and countersign its canonical
     signed_root_payload with the NEW key. Persist the signature +
     new-key fingerprint on the historical row so a post-rotation
     verifier can prove chain continuity across the boundary without
     waiting for the first fresh batch under the new key.
  5. INSERT into ``transparency_historical_keys`` with the cross-signature
     fields populated (or NULL for the very first rotation, before any
     root has been signed).
  6. Atomically replace the on-disk key with the new private PEM (and
     leave a timestamped backup of the previous file next to it).
  7. Print the new fingerprint + the operator instruction to restart the
     audit service so it reloads the singleton.

`--dry-run` performs steps 1+2's *read* and shows what would happen without
writing anywhere. `--execute` does the rotation. The DB insert and disk
write are independent — if the DB insert fails, the script aborts before
touching the key file, so the system never enters a state where the new
key is active but the old key is unknown.

This script is idempotent in the sense that re-running it on an already
rotated system generates yet another key — there's no notion of "already
rotated." For unattended rotation, schedule from outside (e.g. cron + the
maintenance window).

Usage:

    # preview only
    DATABASE_URL=postgresql+asyncpg://... \
      python scripts/maintenance/rotate_transparency_key.py --dry-run

    # actually rotate
    DATABASE_URL=postgresql+asyncpg://... \
      python scripts/maintenance/rotate_transparency_key.py --execute \
        --key-path /data/keys/root-signing.pem \
        --reason "scheduled-quarterly-rotation"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Allow `python scripts/maintenance/rotate_transparency_key.py` from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.audit.models import (  # noqa: E402
    TransparencyHistoricalKey,
    TransparencyRoot,
)
from services.audit.signer import canonical_json, fingerprint_public_key  # noqa: E402

logger = structlog.get_logger(__name__)

DEFAULT_KEY_PATH = "/data/keys/root-signing.pem"


def _load_private_pem(path: Path) -> ed25519.Ed25519PrivateKey:
    pem = path.read_bytes()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError(f"Key at {path} is not ed25519")
    return key


def _public_pem(priv: ed25519.Ed25519PrivateKey) -> bytes:
    return priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _private_pem(priv: ed25519.Ed25519PrivateKey) -> bytes:
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


async def _fetch_last_root_for_key(
    db: AsyncSession, *, signing_fp: str,
) -> TransparencyRoot | None:
    """Return the most recent TransparencyRoot signed by ``signing_fp``,
    or None if this key has not signed a root yet (first rotation on a
    fresh deployment). Ordered by root_date desc — ties are broken by
    the primary key composite ordering which is deterministic per
    tenant, root_date."""
    return (
        await db.execute(
            select(TransparencyRoot)
            .where(TransparencyRoot.signing_key_fingerprint == signing_fp)
            .order_by(TransparencyRoot.root_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _cross_sign_payload(
    new_priv: ed25519.Ed25519PrivateKey,
    signed_root_payload: dict,
) -> str:
    """Return base64(urlsafe, no pad) Ed25519 signature of the
    canonical-JSON form of the OLD key's signed_root_payload, minted
    by the NEW key. The payload is treated as an opaque canonical
    document — this is why the historical row also stores the exact
    root_hash it corresponds to (belt + suspenders for the verifier)."""
    sig = new_priv.sign(canonical_json(signed_root_payload))
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii")


async def _record_historical_key(
    db: AsyncSession,
    *,
    fingerprint: str,
    public_key_pem: bytes,
    retired_reason: str | None,
    transition_root_hash: str | None = None,
    transition_new_key_signature: str | None = None,
    transition_new_key_fingerprint: str | None = None,
) -> bool:
    """Insert into transparency_historical_keys. Returns True if a row was
    inserted, False on idempotent re-run (fingerprint already present).

    The three ``transition_*`` fields together capture the ATF §14.5
    cross-signature; they're populated when the retiring key has
    already signed at least one TransparencyRoot, and NULL on the very
    first rotation of a fresh deployment.
    """
    stmt = (
        pg_insert(TransparencyHistoricalKey)
        .values(
            id=uuid.uuid4(),
            fingerprint=fingerprint,
            public_key_pem=public_key_pem.decode("ascii"),
            algorithm="ed25519",
            rotated_at=datetime.now(tz=UTC),
            retired_reason=retired_reason,
            transition_root_hash=transition_root_hash,
            transition_new_key_signature=transition_new_key_signature,
            transition_new_key_fingerprint=transition_new_key_fingerprint,
        )
        .on_conflict_do_nothing(index_elements=["fingerprint"])
    )
    result = await db.execute(stmt)
    await db.commit()
    return (result.rowcount or 0) > 0


def _backup_existing_key(key_path: Path) -> Path:
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = key_path.with_name(f"{key_path.stem}.{ts}.bak{key_path.suffix}")
    backup.write_bytes(key_path.read_bytes())
    with contextlib.suppress(OSError):
        backup.chmod(0o600)
    return backup


async def rotate(
    *,
    database_url: str,
    key_path: Path,
    dry_run: bool,
    retired_reason: str | None,
) -> dict:
    if not key_path.exists():
        raise SystemExit(
            f"key path does not exist: {key_path}. Set --key-path or run "
            "the audit service once to generate an initial key."
        )

    old_priv = _load_private_pem(key_path)
    old_pub_pem = _public_pem(old_priv)
    old_fp = fingerprint_public_key(old_pub_pem)

    new_priv = ed25519.Ed25519PrivateKey.generate()
    new_pub_pem = _public_pem(new_priv)
    new_fp = fingerprint_public_key(new_pub_pem)

    summary = {
        "old_fingerprint": old_fp,
        "new_fingerprint": new_fp,
        "key_path":        str(key_path),
        "dry_run":         dry_run,
        "retired_reason":  retired_reason,
    }

    if dry_run:
        logger.info("rotation_preview", **summary)
        return summary

    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as db:
            # ATF §14.5 cross-signing — countersign the retiring key's
            # last root with the new key BEFORE inserting the historical
            # row, so the row is written with the transition fields
            # populated in one atomic INSERT.
            last_root = await _fetch_last_root_for_key(db, signing_fp=old_fp)
            transition_root_hash = None
            transition_sig_b64 = None
            transition_new_fp = None
            if last_root is not None:
                transition_root_hash = last_root.root_hash
                transition_sig_b64 = _cross_sign_payload(
                    new_priv, last_root.signed_root_payload,
                )
                transition_new_fp = new_fp
            inserted = await _record_historical_key(
                db,
                fingerprint=old_fp,
                public_key_pem=old_pub_pem,
                retired_reason=retired_reason,
                transition_root_hash=transition_root_hash,
                transition_new_key_signature=transition_sig_b64,
                transition_new_key_fingerprint=transition_new_fp,
            )
        summary["historical_row_inserted"] = inserted
        summary["cross_signed_root_hash"] = transition_root_hash
    finally:
        await engine.dispose()

    backup = _backup_existing_key(key_path)
    summary["backup_path"] = str(backup)

    key_path.write_bytes(_private_pem(new_priv))
    with contextlib.suppress(OSError):
        key_path.chmod(0o600)

    logger.info("rotation_complete", **summary)
    return summary


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) preview only")
    mode.add_argument("--execute", action="store_true", help="apply the rotation")
    p.add_argument("--key-path", default=os.environ.get("ROOT_SIGNING_KEY_PATH", DEFAULT_KEY_PATH))
    p.add_argument("--reason", default=None, help="retired_reason recorded with the historical row")
    p.add_argument("--database-url", default=None, help="override DATABASE_URL (acp_audit DB)")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    dry_run = not args.execute

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not dry_run and not database_url:
        print("ERROR: DATABASE_URL not set and --database-url not provided", file=sys.stderr)
        return 2

    summary = asyncio.run(rotate(
        database_url=database_url or "postgresql+asyncpg://stub",
        key_path=Path(args.key_path),
        dry_run=dry_run,
        retired_reason=args.reason,
    ))

    mode = "DRY-RUN" if dry_run else "EXECUTE"
    print(f"[{mode}] old={summary['old_fingerprint']} new={summary['new_fingerprint']} key={summary['key_path']}")
    if not dry_run:
        print(
            f"  historical row inserted: {summary.get('historical_row_inserted')}\n"
            f"  backup of old key:       {summary.get('backup_path')}\n"
            "  RESTART the audit service so the new key becomes active:\n"
            "    docker compose restart audit"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
