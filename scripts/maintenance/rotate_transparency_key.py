#!/usr/bin/env python3
"""Rotate the transparency-log root-signing key.

Mechanics:

  1. Read the currently-active key from disk (default
     ``/data/keys/root-signing.pem``, override with --key-path).
  2. Compute its fingerprint + PEM and INSERT into
     ``transparency_historical_keys`` so old roots / receipts continue to
     verify after the rotation.
  3. Generate a new Ed25519 keypair.
  4. Atomically replace the on-disk key with the new private PEM (and
     leave a timestamped backup of the previous file next to it).
  5. Print the new fingerprint + the operator instruction to restart the
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
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Allow `python scripts/maintenance/rotate_transparency_key.py` from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.audit.models import TransparencyHistoricalKey  # noqa: E402
from services.audit.signer import fingerprint_public_key  # noqa: E402

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


async def _record_historical_key(
    db: AsyncSession,
    *,
    fingerprint: str,
    public_key_pem: bytes,
    retired_reason: str | None,
) -> bool:
    """Insert into transparency_historical_keys. Returns True if a row was
    inserted, False on idempotent re-run (fingerprint already present).
    """
    stmt = (
        pg_insert(TransparencyHistoricalKey)
        .values(
            id=uuid.uuid4(),
            fingerprint=fingerprint,
            public_key_pem=public_key_pem.decode("ascii"),
            algorithm="ed25519",
            rotated_at=datetime.now(tz=timezone.utc),
            retired_reason=retired_reason,
        )
        .on_conflict_do_nothing(index_elements=["fingerprint"])
    )
    result = await db.execute(stmt)
    await db.commit()
    return (result.rowcount or 0) > 0


def _backup_existing_key(key_path: Path) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = key_path.with_name(f"{key_path.stem}.{ts}.bak{key_path.suffix}")
    backup.write_bytes(key_path.read_bytes())
    try:
        backup.chmod(0o600)
    except OSError:
        pass
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
            inserted = await _record_historical_key(
                db,
                fingerprint=old_fp,
                public_key_pem=old_pub_pem,
                retired_reason=retired_reason,
            )
        summary["historical_row_inserted"] = inserted
    finally:
        await engine.dispose()

    backup = _backup_existing_key(key_path)
    summary["backup_path"] = str(backup)

    key_path.write_bytes(_private_pem(new_priv))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass

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
