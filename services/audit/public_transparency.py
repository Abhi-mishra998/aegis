"""
Public Transparency Publisher — Sprint B 2026-06-14.

The critique that this closes: *the audit chain is self-referential*.
Aegis verifies its own bundle with its own verifier on its own audit log.
A real auditor will not accept "Aegis says Aegis is fine".

This module mirrors every daily Merkle root to a public S3 bucket so an
external witness can:
  1. Fetch the root for any (tenant, date) without an Aegis API key.
  2. Compare the signature against the published signing key.
  3. Verify the prev_root_hash chain forms an unbroken sequence.

Bucket layout:
    s3://<AEGIS_PUBLIC_ROOTS_BUCKET>/
        keys/<signing_kid>.pem              # public key (one-time write)
        roots/<tenant_id>/<YYYY-MM-DD>.json  # one per (tenant, day)
        latest.json                          # most-recent (tenant, day, root)

Each <date>.json is a frozen view of one TransparencyRoot row plus enough
context to verify it offline.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Default to the prod-ha public bucket. Override via env for self-hosted
# tenants or to disable mirroring entirely (set to empty string).
_PUBLIC_BUCKET = os.environ.get(
    "AEGIS_PUBLIC_ROOTS_BUCKET",
    "aegis-public-roots-628478946931",
)
_AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# Lazy boto3 import — keeps the audit container slim when the bucket is
# not configured (e.g. self-hosted dev tenants).
_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        try:
            import boto3  # noqa: F401
            _s3_client = boto3.client("s3", region_name=_AWS_REGION)
        except Exception as exc:
            log.warning("public_root_boto3_unavailable", error=str(exc))
            return None
    return _s3_client


def is_enabled() -> bool:
    return bool(_PUBLIC_BUCKET)


def publish_root(
    *,
    tenant_id: str,
    root_date: date,
    root_hash: str,
    leaf_count: int,
    signed_payload: dict[str, Any],
    prev_root_hash: str | None,
    signing_kid: str,
) -> bool:
    """Publish one daily root to the public S3 ledger.

    Returns True when an object was uploaded; False on no-op or failure.
    Never raises — the audit service must keep sealing local roots even
    when the public mirror is unreachable.
    """
    if not is_enabled():
        return False
    s3 = _get_s3()
    if s3 is None:
        return False

    body = {
        "format":           "aegis-public-root/2026-06",
        "tenant_id":        str(tenant_id),
        "root_date":        root_date.isoformat(),
        "root_hash":        root_hash,
        "leaf_count":       leaf_count,
        "prev_root_hash":   prev_root_hash,
        "signing_kid":      signing_kid,
        "signed_payload":   signed_payload,
        "published_at":     int(time.time()),
        "notes": (
            "External witness: download this file directly (no Aegis "
            "credentials required). Verify the signature against the "
            "public key at /keys/<signing_kid>.pem. Walk the prev_root_hash "
            "chain back to genesis to detect rewrite."
        ),
    }
    key_root = f"roots/{tenant_id}/{root_date.isoformat()}.json"
    key_latest = f"latest/{tenant_id}.json"

    try:
        for k in (key_root, key_latest):
            s3.put_object(
                Bucket=_PUBLIC_BUCKET,
                Key=k,
                Body=json.dumps(body, indent=2, sort_keys=True).encode(),
                ContentType="application/json",
                CacheControl="public, max-age=300",
            )
        log.info(
            "public_root_published",
            tenant_id=str(tenant_id),
            root_date=root_date.isoformat(),
            bucket=_PUBLIC_BUCKET,
            key=key_root,
        )
        return True
    except Exception as exc:
        log.warning(
            "public_root_publish_failed",
            tenant_id=str(tenant_id),
            root_date=root_date.isoformat(),
            error=str(exc),
        )
        return False


def publish_signing_key(kid: str, public_pem: str) -> bool:
    """One-time write of the public signing key. Skipped if the object exists.

    Safe to call on every audit-service boot — boto3 `HeadObject` short-
    circuits on a no-op when the key is already present.
    """
    if not is_enabled():
        return False
    s3 = _get_s3()
    if s3 is None:
        return False
    key = f"keys/{kid}.pem"
    try:
        s3.head_object(Bucket=_PUBLIC_BUCKET, Key=key)
        return False  # already published
    except Exception:
        pass
    try:
        s3.put_object(
            Bucket=_PUBLIC_BUCKET, Key=key, Body=public_pem.encode(),
            ContentType="application/x-pem-file",
            CacheControl="public, max-age=86400",
        )
        log.info("public_signing_key_published", kid=kid, bucket=_PUBLIC_BUCKET)
        return True
    except Exception as exc:
        log.warning("public_signing_key_publish_failed", kid=kid, error=str(exc))
        return False
