#!/usr/bin/env python3
"""QA-CRYPTO-FIX (2026-06-24) — one-shot cleanup for leftover smoke / test
keys in the public transparency bucket.

The pre-launch audit (2026-06-24) found ``keys/smoke-kid.pem`` (57 bytes,
last modified 2026-06-14) in ``aegis-public-roots-628478946931`` next to
the real ed25519 rotation history. Any auditor walking
``GET /transparency/keys`` → S3 sees a key whose kid is literally
"smoke-kid" and asks why. The publisher in
``services/audit/public_transparency.py`` now refuses to push such kids
(see ``_is_smoke_kid``), but the historical artefacts need an explicit
delete.

Usage::

    AWS_PROFILE=aegis-prod-ops python3 scripts/ops/cleanup_public_smoke_keys.py --dry-run
    AWS_PROFILE=aegis-prod-ops python3 scripts/ops/cleanup_public_smoke_keys.py --execute

Idempotent: re-running after the delete is a no-op. Targets a known
allow-list of kids by exact match — does NOT pattern-delete arbitrary
keys, so a mistaken run cannot wipe the real rotation history.
"""
from __future__ import annotations

import argparse
import sys


# Exact-match kid → S3 object key. Keep this list explicit; do NOT
# replace with a wildcard. The point of this script is to be a typo
# away from "delete one specific known-leftover artefact", not a
# bulk-delete tool.
TARGETS: list[tuple[str, str]] = [
    ("smoke-kid", "keys/smoke-kid.pem"),
]

BUCKET = "aegis-public-roots-628478946931"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Default. Reports what would be deleted; does not call S3.")
    ap.add_argument("--execute", action="store_true",
                    help="Actually delete the targets. Required to make changes.")
    ap.add_argument("--bucket", default=BUCKET,
                    help=f"Override the public-transparency bucket (default: {BUCKET}).")
    args = ap.parse_args()

    if args.execute:
        args.dry_run = False

    try:
        import boto3
    except ImportError:
        print("FATAL: pip install boto3", file=sys.stderr)
        return 2

    s3 = boto3.client("s3")
    found = 0
    deleted = 0
    missing = 0
    for kid, key in TARGETS:
        try:
            s3.head_object(Bucket=args.bucket, Key=key)
            present = True
        except Exception:
            present = False
        if not present:
            print(f"  [SKIP] {kid:24s} object {key!r} not present in {args.bucket}")
            missing += 1
            continue
        found += 1
        if args.dry_run:
            print(f"  [DRY ] {kid:24s} would DELETE s3://{args.bucket}/{key}")
            continue
        try:
            s3.delete_object(Bucket=args.bucket, Key=key)
            print(f"  [DEL ] {kid:24s} deleted s3://{args.bucket}/{key}")
            deleted += 1
        except Exception as exc:
            print(f"  [ERR ] {kid:24s} delete failed: {exc}", file=sys.stderr)

    print()
    print(f"summary: targets={len(TARGETS)} found={found} deleted={deleted} missing={missing} dry_run={args.dry_run}")
    if args.dry_run and found > 0:
        print("re-run with --execute to actually delete the listed objects.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
