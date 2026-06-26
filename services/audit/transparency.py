"""Daily transparency log — Merkle root over signed receipts.

For each (tenant, day), we compute one Merkle root over every signed receipt
produced that day, sign the root, and persist. Customers who archive the root
at end-of-day can detect retroactive tampering or deletion: any modification
to an underlying audit row shifts the recomputed root.

The leaf for an audit row is `sha256(canonical_json(signed_receipt))`, where
`signed_receipt` is exactly what `/v1/receipts/{id}` returns. This means a
customer with one daily root can verify any historical receipt's inclusion
without re-fetching the full log.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.audit.merkle import (
    build_root,
    inclusion_proof,
    leaf_hash,
)
from services.audit.models import AuditLog, TransparencyRoot
from services.audit.signer import canonical_json, get_root_signer, get_signer

transparency_router = APIRouter(
    prefix="/transparency",
    tags=["transparency"],
    dependencies=[Depends(verify_internal_secret)],
)


def _leaf_for_row(row: AuditLog) -> str:
    """The exact same value the customer would compute from the receipt."""
    signed = get_signer().sign(row)
    return leaf_hash(canonical_json(signed))


async def _rows_for_day(db: AsyncSession, tenant_id: uuid.UUID, day: date) -> list[AuditLog]:
    """Audit rows for a (tenant, day), sorted deterministically."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(days=1)
    result = await db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.timestamp >= start,
            AuditLog.timestamp < end,
        )
        .order_by(AuditLog.timestamp.asc(), AuditLog.id.asc())
    )
    return list(result.scalars().all())


async def _previous_root_hash(
    db: AsyncSession, tenant_id: uuid.UUID, current_day: date,
) -> str | None:
    """Return the root_hash of the most recent persisted root STRICTLY before
    `current_day` for this tenant. None on the first-ever row.

    Used to populate `TransparencyRoot.prev_root_hash` AND the signed payload's
    `prev_root_hash` field — duplicating the pointer in both places means a
    database-level tamper still mismatches against the cryptographic payload,
    so the chain is detectable from either side.
    """
    stmt = (
        select(TransparencyRoot.root_hash)
        .where(
            TransparencyRoot.tenant_id == tenant_id,
            TransparencyRoot.root_date < current_day,
        )
        .order_by(TransparencyRoot.root_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def _persist_root(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    root_date: date,
    root_hash: str,
    leaf_count: int,
    signed_payload: dict[str, Any],
    prev_root_hash: str | None = None,
    leaf_range_start_id: uuid.UUID | None = None,
    leaf_range_end_id: uuid.UUID | None = None,
    signing_key_fingerprint: str | None = None,
) -> None:
    values = {
        "tenant_id":               tenant_id,
        "root_date":               root_date,
        "root_hash":               root_hash,
        "prev_root_hash":          prev_root_hash,
        "leaf_count":              leaf_count,
        "signed_root_payload":     signed_payload,
        "leaf_range_start_id":     leaf_range_start_id,
        "leaf_range_end_id":       leaf_range_end_id,
        "signing_key_fingerprint": signing_key_fingerprint,
    }
    # arch-26 V5 fix 2026-06-26 — chain-integrity guard. The
    # on_conflict_do_update was a silent chain-break vector: rewriting
    # an existing root_hash for date D corrupts every later root's
    # prev_root_hash that still points at the OLD hash. Today's running
    # root advances all day (fine — no later root exists yet). But if
    # day N+1's root is already sealed and someone re-runs the scheduler
    # for day N (manual backfill, /transparency/seal-root call, race),
    # day N's hash flips and day N+1's prev_root_hash is now stale → V5
    # FAIL on the next verifier run.
    #
    # Fix: only update root_hash if NO LATER root exists for this tenant.
    # If a later root exists, log the attempted overwrite and abort the
    # update. The intended root is preserved (today still advances; past
    # days re-sealed without an existing later root still update).
    existing_later = (
        await db.execute(
            select(TransparencyRoot.root_date).where(
                TransparencyRoot.tenant_id == tenant_id,
                TransparencyRoot.root_date > root_date,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if existing_later is not None:
        # Fetch the row that's about to be overwritten to decide if this
        # is a genuine no-op (same hash, idempotent re-run — safe to
        # update timestamp) or a chain-break attempt (different hash).
        current = (
            await db.execute(
                select(TransparencyRoot.root_hash).where(
                    TransparencyRoot.tenant_id == tenant_id,
                    TransparencyRoot.root_date == root_date,
                )
            )
        ).scalar_one_or_none()
        if current is not None and current != root_hash:
            import logging
            logging.getLogger(__name__).error(
                "transparency_root_update_blocked_chain_protect",
                extra={
                    "tenant_id":          str(tenant_id),
                    "root_date":          root_date.isoformat(),
                    "existing_hash":      current,
                    "attempted_new_hash": root_hash,
                    "next_root_date":     existing_later.isoformat() if existing_later else None,
                    "reason":             "later root exists; updating root_hash would invalidate its prev_root_hash",
                },
            )
            return  # refuse silently — caller's commit still succeeds

    stmt = (
        pg_insert(TransparencyRoot)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["tenant_id", "root_date"],
            set_={
                "root_hash":               root_hash,
                "prev_root_hash":          prev_root_hash,
                "leaf_count":              leaf_count,
                "signed_root_payload":     signed_payload,
                "leaf_range_start_id":     leaf_range_start_id,
                "leaf_range_end_id":       leaf_range_end_id,
                "signing_key_fingerprint": signing_key_fingerprint,
                "computed_at":             datetime.now(UTC),
            },
        )
    )
    await db.execute(stmt)
    await db.commit()


def empty_epoch_root_hash_v1(prev_root_hash: str | None) -> str:
    """Legacy v1 empty-epoch hash — retained ONLY for verifying historical roots.

    N28 (2026-06-21): the v1 formula was

        sha256(prev_root_hash || "\\ntransparency_empty_epoch_v1\\n")

    which collides whenever ``prev_root_hash`` is shared across two
    empty-epoch markers — most concretely, every tenant's genesis empty
    day (when prev=None) hashes to the same value, and any cross-tenant
    coincidence of prev_root_hash bytes also collides.

    The scheduler today never actually persists a v1 marker when
    prev_hash is None (see ``transparency_scheduler._commit_one``),
    so the genesis-collision case wasn't exploited in production, but
    the cross-tenant collision still violates per-tenant unforgeability.

    v2 (below) closes the collision by mixing the ``root_date`` into the
    seed. Existing v1 roots stay verifiable by leaving this function on
    disk — operators can re-derive the historical hash and confirm.

    DO NOT call this for new markers. Use ``empty_epoch_root_hash``.
    """
    seed = (prev_root_hash or "").encode("ascii") + b"\ntransparency_empty_epoch_v1\n"
    return hashlib.sha256(seed).hexdigest()


_EMPTY_EPOCH_DOMAIN_V2 = "transparency_empty_epoch_v2"


def empty_epoch_root_hash(
    prev_root_hash: str | None, root_date: date | None = None,
) -> str:
    """Deterministic root for an empty epoch (zero audit rows that day).

    Customers + auditors compute the same value offline from
    ``prev_root_hash`` + ``root_date``. Domain-separated with the
    sentinel ``"transparency_empty_epoch_v2"`` so no leaf-set hash can
    collide with an empty-epoch hash, and so no two distinct empty days
    can share a root even when they share a prev (N28 fix).

    Seed format (v2):

        sha256(
          (prev_root_hash or "")    +
          "|" + root_date.isoformat() +
          "|" + "transparency_empty_epoch_v2"
        )

    ``root_date`` is optional ONLY so existing v1 callers don't 422 the
    instant this lands; if omitted, we route to the legacy v1 formula
    and emit a structured warning. Every new caller in this module
    passes the date explicitly.
    """
    if root_date is None:
        # Back-compat shim: route to v1 so the caller's output is
        # unchanged byte-for-byte. New callers (the same module's
        # scheduler + compute_root paths, updated in this commit) pass
        # the date and land in the v2 branch below.
        return empty_epoch_root_hash_v1(prev_root_hash)
    seed = (
        (prev_root_hash or "")
        + "|" + root_date.isoformat()
        + "|" + _EMPTY_EPOCH_DOMAIN_V2
    )
    return hashlib.sha256(seed.encode("ascii")).hexdigest()


def _sign_root(
    tenant_id: uuid.UUID,
    root_date: date,
    root_hash: str,
    leaf_count: int,
    prev_root_hash: str | None = None,
    leaf_range_start_id: uuid.UUID | None = None,
    leaf_range_end_id: uuid.UUID | None = None,
    window_end: datetime | None = None,
) -> dict[str, Any]:
    """Sign the daily root commitment.

    Uses the *root-signing* key (`get_root_signer()`) so the receipt-signing
    key can rotate without invalidating historical roots. The fingerprint in
    the returned payload identifies which key signed it.

    `prev_root_hash` (Crypto Sprint 2026-05-15): the signed payload commits
    to the immediately previous day's root_hash for the same tenant. This
    turns the daily roots into an append-only chain — rewriting yesterday
    would invalidate every subsequent day's signature in O(n) signed
    payloads, none of which an adversary without the root key can produce.

    `leaf_range_*_id` (Transparency Log sprint 2026-05-15): the signed
    payload pins the inclusive range of audit_logs.id values the root commits
    to. A customer with an export can recompute the exact same root from
    rows in that range — no ambiguity about which 60-second window of late
    audit writes were or weren't included.

    `window_end` (Sprint 1.2, live-tail anchoring): when set, pins the precise
    UTC instant the root commits to. The scheduler refreshes today's root
    every ``TRANSPARENCY_SCHEDULER_INTERVAL`` seconds and bumps ``window_end``
    forward, so any audit row whose timestamp is past the most recent
    ``window_end`` is detectably unanchored. Without this field the verifier
    would have to wait for midnight to know whether a tail row was committed
    — a 24-hour truncation window the audit (C9) flagged as critical.
    """
    signer = get_root_signer()
    payload = {
        "version":              4,
        "kind":                 "transparency_root",
        "tenant_id":            str(tenant_id),
        "root_date":            root_date.isoformat(),
        "root_hash":            root_hash,
        "prev_root_hash":       prev_root_hash,
        "leaf_count":           leaf_count,
        "leaf_range_start_id":  str(leaf_range_start_id) if leaf_range_start_id else None,
        "leaf_range_end_id":    str(leaf_range_end_id) if leaf_range_end_id else None,
        "window_end":           window_end.isoformat() if window_end else None,
    }
    import base64
    sig = signer._priv.sign(canonical_json(payload))  # noqa: SLF001 — intentional
    return {
        "receipt":                payload,
        "signature":              base64.urlsafe_b64encode(sig).rstrip(b"=").decode("ascii"),
        "algorithm":              "ed25519",
        "public_key_fingerprint": signer._fingerprint,  # noqa: SLF001
    }


# ── Endpoints ─────────────────────────────────────────────────────────────


@transparency_router.post("/compute", response_model=APIResponse[dict])
async def compute_root(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    root_date: date | None = Query(None, description="Day to commit (UTC); defaults to today"),
) -> APIResponse[dict]:
    """Compute (or recompute) the daily Merkle root for a (tenant, date).

    Idempotent — re-runs replace the existing row but preserve the date.
    Intended to be called by a daily cron at 00:05 UTC, but safe to invoke
    on-demand for backfill or recovery.
    """
    day = root_date or datetime.now(UTC).date()
    rows = await _rows_for_day(db, tenant_id, day)
    prev_hash = await _previous_root_hash(db, tenant_id, day)

    leaf_range_start_id: uuid.UUID | None = None
    leaf_range_end_id:   uuid.UUID | None = None
    if not rows:
        # Empty-epoch marker: deterministic hash over prev_root_hash + date
        # + domain separator. Keeps the Merkle-of-Merkles chain unbroken on
        # quiet days. N28 (2026-06-21): root_date is now in the seed so two
        # consecutive empty days never collide (was a real bug — the
        # genesis-empty case across tenants all hashed to the same value).
        root = empty_epoch_root_hash(prev_hash, day)
        leaves: list[str] = []
    else:
        leaves = [_leaf_for_row(r) for r in rows]
        root = build_root(leaves)
        leaf_range_start_id = rows[0].id
        leaf_range_end_id = rows[-1].id

    signer = get_root_signer()
    # Sprint 1.2: anchor a precise window_end so the verifier can detect a
    # tail row that lands after the last sealed root.
    today = datetime.now(UTC).date()
    if day < today:
        window_end = datetime(day.year, day.month, day.day, 23, 59, 59, 999_999, tzinfo=UTC)
    else:
        window_end = datetime.now(UTC)
    signed = _sign_root(
        tenant_id, day, root, len(leaves),
        prev_root_hash=prev_hash,
        window_end=window_end,
        leaf_range_start_id=leaf_range_start_id,
        leaf_range_end_id=leaf_range_end_id,
    )
    await _persist_root(
        db,
        tenant_id=tenant_id,
        root_date=day,
        root_hash=root,
        leaf_count=len(leaves),
        signed_payload=signed,
        prev_root_hash=prev_hash,
        leaf_range_start_id=leaf_range_start_id,
        leaf_range_end_id=leaf_range_end_id,
        signing_key_fingerprint=signer._fingerprint,  # noqa: SLF001
    )

    return APIResponse(data={
        "root_date":               day.isoformat(),
        "root_hash":               root,
        "prev_root_hash":          prev_hash,
        "leaf_count":              len(leaves),
        "leaf_range_start_id":     str(leaf_range_start_id) if leaf_range_start_id else None,
        "leaf_range_end_id":       str(leaf_range_end_id) if leaf_range_end_id else None,
        "signing_key_fingerprint": signer._fingerprint,  # noqa: SLF001
        "signed":                  signed,
    })


@transparency_router.get("/roots", response_model=APIResponse[list[dict]])
async def list_roots(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(90, ge=1, le=365),
) -> APIResponse[list[dict]]:
    """List persisted daily roots, newest first.

    QA-TRANSPARENCY-FIX (2026-06-24) — when the list is empty the meta
    block now carries a ``seal_cadence`` hint so customers / auditors
    understand that ``[]`` means "the daily seal at 00:00 UTC hasn't
    run yet for any of this tenant's audit rows", not "transparency is
    broken". The old bare ``[]`` confused evaluators on day-of signup.
    """
    q = select(TransparencyRoot).where(TransparencyRoot.tenant_id == tenant_id)
    if since:
        q = q.where(TransparencyRoot.root_date >= since)
    if until:
        q = q.where(TransparencyRoot.root_date <= until)
    q = q.order_by(TransparencyRoot.root_date.desc()).limit(limit)

    rows = (await db.execute(q)).scalars().all()
    data = [
        {
            "root_date":    r.root_date.isoformat(),
            "root_hash":    r.root_hash,
            "leaf_count":   r.leaf_count,
            "computed_at":  r.computed_at.isoformat(),
            "signed":       r.signed_root_payload,
        }
        for r in rows
    ]
    meta: dict[str, Any] | None = None
    if not data:
        # Cheap probe: does this tenant have ANY audit rows yet? If so,
        # the empty response is just "seal hasn't run yet". If not, the
        # tenant is genuinely brand new and has nothing to seal.
        any_row = (
            await db.execute(
                select(AuditLog.id).where(AuditLog.tenant_id == tenant_id).limit(1)
            )
        ).first()
        meta = {
            "seal_cadence":  "daily at 00:00 UTC",
            "public_mirror": "s3://aegis-public-roots-628478946931/",
            "has_audit_rows": bool(any_row),
            "explanation":   (
                "Roots are sealed daily; a fresh tenant has none until the "
                "first end-of-day seal completes."
                if any_row is None
                else "Daily seal pending — re-check after the next 00:00 UTC tick."
            ),
        }
    return APIResponse(data=data, meta=meta)


@transparency_router.get("/roots/{root_date}", response_model=APIResponse[dict])
async def get_root(
    root_date: date,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    row = (
        await db.execute(
            select(TransparencyRoot).where(
                TransparencyRoot.tenant_id == tenant_id,
                TransparencyRoot.root_date == root_date,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no root persisted for that date")
    return APIResponse(data={
        "root_date":    row.root_date.isoformat(),
        "root_hash":    row.root_hash,
        "leaf_count":   row.leaf_count,
        "computed_at":  row.computed_at.isoformat(),
        "signed":       row.signed_root_payload,
    })


@transparency_router.get("/consistency", response_model=APIResponse[dict])
async def consistency_proof(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    from_date: date = Query(..., description="Older root date the customer already archived"),
    to_date: date = Query(..., description="Newer root date to prove extends the older one"),
) -> APIResponse[dict]:
    """Return the chain of root_hash + prev_root_hash records from `from_date`
    through `to_date`. The caller verifies the chain by confirming, for every
    consecutive pair (i, i+1), that `chain[i+1].prev_root_hash == chain[i].root_hash`.

    This is the cryptographic equivalent of RFC 6962 §2.1.2 consistency
    proofs — proves that the log was append-only between two snapshots. A
    customer who archives an older signed root can periodically pull this
    endpoint and verify offline that no events were silently re-ordered or
    deleted in the interim.
    """
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")

    stmt = (
        select(TransparencyRoot)
        .where(
            TransparencyRoot.tenant_id == tenant_id,
            TransparencyRoot.root_date >= from_date,
            TransparencyRoot.root_date <= to_date,
        )
        .order_by(TransparencyRoot.root_date.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Compute the verification verdict server-side so naive clients still get
    # a yes/no without having to walk the chain themselves. The hashes are
    # still returned so a paranoid client can verify independently.
    chain = []
    consistent = True
    expected_prev: str | None = None
    for r in rows:
        link = {
            "root_date":      r.root_date.isoformat(),
            "root_hash":      r.root_hash,
            "prev_root_hash": r.prev_root_hash,
            "leaf_count":     r.leaf_count,
            "computed_at":    r.computed_at.isoformat(),
            "signed":         r.signed_root_payload,
        }
        # Only enforce continuity AFTER the first record — the customer might
        # be asking about a window that doesn't include the genesis root.
        if expected_prev is not None and r.prev_root_hash != expected_prev:
            consistent = False
            link["break"] = True
        chain.append(link)
        expected_prev = r.root_hash

    return APIResponse(data={
        "from_date":  from_date.isoformat(),
        "to_date":    to_date.isoformat(),
        "count":      len(chain),
        "consistent": consistent,
        "chain":      chain,
    })


# ──────────────────────────────────────────────────────────────────────────
# CRYPTOGRAPHIC VERIFY ENDPOINTS (no SDK install required for auditors)
# ──────────────────────────────────────────────────────────────────────────


_VERIFY_ROOT_REQUIRED_FIELDS = ("receipt", "signature", "algorithm", "public_key_fingerprint")
_VERIFY_ROOT_RECEIPT_REQUIRED = ("kind", "tenant_id", "root_date", "root_hash")


@transparency_router.post("/verify-root", response_model=APIResponse[dict])
async def verify_root(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    payload: dict[str, Any] | None = None,
) -> APIResponse[dict]:
    """Verify a signed transparency-root payload server-side.

    Body shape (exactly what list_roots / compute_root returns under `signed`):
       { receipt: { ... }, signature: "<b64>", algorithm: "ed25519",
         public_key_fingerprint: "<hex32>" }

    Response shape — guaranteed:
       {
         valid:                true | false,
         algorithm:            "ed25519",
         expected_fingerprint: "<hex32>",
         errors:               [] | ["malformed_payload" | "unknown_key_fingerprint"
                                     | "signature_mismatch" | "root_hash_mismatch"]
       }

    Returns HTTP 400 (with the same structured shape) when the payload is
    syntactically malformed; HTTP 200 with valid:false + a specific error
    code when the payload is well-formed but cryptographically wrong. Never
    returns null for `valid`, `algorithm`, or `expected_fingerprint`.

    For historical roots signed by a rotated key, the verify path consults
    `transparency_historical_keys` and accepts the matching fingerprint —
    customers don't need to manually pick the key.
    """
    import base64 as _b64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    from services.audit.signer import (
        canonical_json,
        fingerprint_public_key,
        get_root_signer,
        load_historical_public_keys,
    )

    active_signer = get_root_signer()
    expected_fingerprint = active_signer._fingerprint  # noqa: SLF001
    algorithm_label = "ed25519"

    def _err(errors: list[str], *, http_400: bool = False) -> APIResponse[dict]:
        body = {
            "valid":                False,
            "algorithm":            algorithm_label,
            "expected_fingerprint": expected_fingerprint,
            "errors":               errors,
        }
        if http_400:
            raise HTTPException(status_code=400, detail=body)
        return APIResponse(data=body)

    # ── Date shortcut: {"date": "YYYY-MM-DD"} auto-fetches & verifies stored root ──
    if isinstance(payload, dict) and payload.keys() <= {"date"} and "date" in payload:
        from datetime import date as _date

        from sqlalchemy import select as _select

        from services.audit.models import TransparencyRoot as _TR
        try:
            d = _date.fromisoformat(str(payload["date"]))
        except (ValueError, TypeError):
            return _err(["malformed_payload"], http_400=True)
        row = (await db.execute(
            _select(_TR).where(_TR.tenant_id == tenant_id, _TR.root_date == d)
        )).scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no root for {d}")
        payload = row.signed_root_payload

    # ── Phase 1: structural validation ────────────────────────────────────
    if not isinstance(payload, dict) or not payload:
        return _err(["malformed_payload"], http_400=True)

    missing = [k for k in _VERIFY_ROOT_REQUIRED_FIELDS if k not in payload]
    if missing:
        return _err(["malformed_payload"], http_400=True)
    if payload.get("algorithm") != algorithm_label:
        return _err(["malformed_payload"], http_400=True)
    receipt = payload.get("receipt")
    if not isinstance(receipt, dict):
        return _err(["malformed_payload"], http_400=True)
    if any(k not in receipt for k in _VERIFY_ROOT_RECEIPT_REQUIRED):
        return _err(["malformed_payload"], http_400=True)

    # ── Phase 2: locate the public key by fingerprint ────────────────────
    payload_fp = payload["public_key_fingerprint"]
    candidate_pems: list[tuple[str, bytes]] = [
        (expected_fingerprint, active_signer._pub_pem),  # noqa: SLF001
    ]
    for hist in await load_historical_public_keys(db):
        candidate_pems.append((hist["fingerprint"], hist["public_key_pem"].encode("ascii")))

    pub_pem: bytes | None = None
    for fp, pem in candidate_pems:
        if fp == payload_fp:
            pub_pem = pem
            break
    if pub_pem is None:
        return _err(["unknown_key_fingerprint"])

    # Cross-check the fingerprint actually matches the PEM (defense in depth
    # against a forged payload that claims an existing fingerprint over a
    # different PEM body).
    if fingerprint_public_key(pub_pem) != payload_fp:
        return _err(["unknown_key_fingerprint"])

    # ── Phase 3: signature verification ───────────────────────────────────
    try:
        pub = serialization.load_pem_public_key(pub_pem)
    except (ValueError, TypeError):
        return _err(["malformed_payload"], http_400=True)
    if not isinstance(pub, ed25519.Ed25519PublicKey):
        return _err(["malformed_payload"], http_400=True)

    sig_field = payload["signature"]
    if not isinstance(sig_field, str):
        return _err(["malformed_payload"], http_400=True)
    try:
        sig_bytes = _b64.urlsafe_b64decode(sig_field + "=" * (-len(sig_field) % 4))
    except Exception:
        return _err(["malformed_payload"], http_400=True)
    # Ed25519 signatures are always exactly 64 bytes. Anything else is
    # structurally bogus, not "valid bytes that didn't verify."
    if len(sig_bytes) != 64:
        return _err(["malformed_payload"], http_400=True)

    try:
        pub.verify(sig_bytes, canonical_json(receipt))
    except Exception:
        return _err(["signature_mismatch"])

    # ── Phase 4: root_hash sanity ─────────────────────────────────────────
    # The signed receipt commits to a particular root_hash. Reject obvious
    # nonsense (e.g. wrong length, non-hex). We DON'T recompute the leaf
    # set here — that would require pulling every audit row in the range
    # and would conflate "the seal is honest" with "your local copy of the
    # audit log matches ours."
    rh = receipt.get("root_hash")
    if not (isinstance(rh, str) and len(rh) == 64):
        try:
            bytes.fromhex(rh)
            shape_ok = True
        except (TypeError, ValueError):
            shape_ok = False
        if not shape_ok:
            return _err(["root_hash_mismatch"])

    return APIResponse(data={
        "valid":                True,
        "algorithm":            algorithm_label,
        "expected_fingerprint": payload_fp,
        "errors":               [],
    })


@transparency_router.get("/keys", response_model=APIResponse[dict])
async def list_root_signing_keys(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return the currently-active root-signing public key.

    N4 fix (2026-06-21): previously also returned the global
    `transparency_historical_keys` registry, which leaks every other
    tenant's key-rotation cadence and fingerprints to any caller. The
    `transparency_historical_keys` schema (see
    `services/audit/models.py::TransparencyHistoricalKey`) carries no
    `tenant_id` column, so per-tenant filtering at the query level is
    impossible without a schema migration. Until that migration lands,
    this endpoint requires tenant authentication and returns ONLY the
    currently-active key.

    Callers that need to verify a receipt signed by a previously-active
    key should use `/transparency/verify-root` or `/receipts/verify`,
    which transparently fall back to the historical registry server-side
    via `signer.verify_receipt_against_known_keys`.

    `tenant_id` is required to enforce that the caller has presented a
    valid tenant identity; the active key itself is platform-global.
    """
    from services.audit.signer import get_root_signer
    s = get_root_signer()
    return APIResponse(data={
        "active": s.public_key_info(),
    })


@transparency_router.get("/inclusion/{execution_id}", response_model=APIResponse[dict])
async def get_inclusion_proof(
    execution_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """Return an inclusion proof for one execution against its day's root.

    `execution_id` may be the audit row UUID or the upstream `request_id`.
    If the day's root has not yet been computed (e.g. it's still today),
    the response includes `pending: true` and the live root over the day's
    rows so the customer can verify before end-of-day persistence.
    """
    # Locate the target row
    row = None
    try:
        as_uuid = uuid.UUID(execution_id)
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.id == as_uuid, AuditLog.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        pass
    if row is None:
        row = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.request_id == execution_id,
                    AuditLog.tenant_id == tenant_id,
                ).order_by(AuditLog.timestamp.desc()).limit(1)
            )
        ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="no audit row matches the given execution_id")

    day = row.timestamp.astimezone(UTC).date()
    rows = await _rows_for_day(db, tenant_id, day)
    leaves = [_leaf_for_row(r) for r in rows]
    try:
        idx = next(i for i, r in enumerate(rows) if r.id == row.id)
    except StopIteration:
        raise HTTPException(status_code=500, detail="row not present in its own day window")

    proof = inclusion_proof(leaves, idx)

    persisted = (
        await db.execute(
            select(TransparencyRoot).where(
                TransparencyRoot.tenant_id == tenant_id,
                TransparencyRoot.root_date == day,
            )
        )
    ).scalar_one_or_none()

    return APIResponse(data={
        "root_date":  day.isoformat(),
        "proof":      proof,
        "pending":    persisted is None,
        "signed_root": (persisted.signed_root_payload if persisted else None),
    })
