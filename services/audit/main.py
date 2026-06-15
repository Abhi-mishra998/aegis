"""
ACP Audit Service — Stream Consumer + FastAPI App
==================================================
Lifespan:
  1. Create DB tables
  2. Ensure Redis Stream consumer group exists
  3. Start background stream consumer task
  4. On shutdown: cancel consumer task + dispose engine

Stream consumer:
  - Reads from "acp:audit_stream" via XREADGROUP
  - Writes each event to PostgreSQL via AuditWriter
  - ACKs each message on success (ensures at-least-once delivery)
  - Handles pending (unacked) messages on startup
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.db import engine, get_db, get_session_factory, get_tenant_id
from sdk.common.migrate import check_schema
from sdk.common.redis import get_redis_client
from sdk.utils import setup_app
from services.audit.database import SessionLocal, settings
from services.audit.outbox_worker import run_outbox_worker
from services.audit.router import pending_router, router
from services.audit.schemas import AuditLogCreate
from services.audit.writer import AuditWriter

logger = structlog.get_logger(__name__)

_STREAM_KEY = "acp:audit_stream"
_DLQ_KEY = "acp:audit_stream:dlq"
_CONSUMER_GROUP = "acp:audit:consumers"
_CONSUMER_NAME = "audit-service-1"
_BLOCK_MS = 2000  # block 2s waiting for new messages
_BATCH_SIZE = 50  # messages per read cycle
_RETRY_SLEEP = 1.0  # seconds to sleep on error


def _parse_stream_event(
    fields: dict[bytes, bytes] | dict[str, str],
) -> tuple[AuditLogCreate | None, dict[str, Any] | None]:
    """Convert raw Redis stream fields to AuditLogCreate schema + billing data.

    Returns: (AuditLogCreate, billing_data_dict) where billing_data is None if not present.
    """
    try:
        # Redis returns bytes when decode_responses=False
        decoded: dict[str, Any] = {
            k.decode() if isinstance(k, bytes) else k: v.decode()
            if isinstance(v, bytes)
            else v
            for k, v in fields.items()
        }

        metadata_raw = decoded.get("metadata_json", "{}")
        try:
            metadata = json.loads(metadata_raw)
        except Exception:
            metadata = {}

        # 2026-05-13 BUGFIX: identity service emits user_login events with
        # agent_id="" (no agent for human logins). uuid.UUID("") raised
        # ValueError and the event went to DLQ. Coerce empty/invalid IDs to
        # the null UUID — the audit log stays attributable to the tenant.
        agent_id_raw = (decoded.get("agent_id") or "").strip()
        try:
            agent_uuid = uuid.UUID(agent_id_raw) if agent_id_raw else uuid.UUID(int=0)
        except ValueError:
            agent_uuid = uuid.UUID(int=0)

        audit_event = AuditLogCreate(
            tenant_id=uuid.UUID(decoded["tenant_id"]),
            agent_id=agent_uuid,
            action=decoded.get("action", "unknown"),
            tool=decoded.get("tool"),
            decision=decoded.get("decision", "unknown"),
            reason=decoded.get("reason"),
            request_id=decoded.get("request_id"),
            metadata_json=metadata,
        )

        # Extract billing data if present (outbox pattern)
        billing_data = None
        if "billing_units" in decoded or "billing_cost" in decoded:
            billing_data = {
                "agent_id": decoded.get("agent_id"),
                "tool": decoded.get("tool", "unknown"),
                "units": int(decoded.get("billing_units", "1")),
                "cost": float(decoded.get("billing_cost", "0.001")),
            }

        return audit_event, billing_data
    except Exception as exc:
        logger.error(
            "audit_event_parse_failed", error=str(exc), fields=str(fields)[:200]
        )
        return None, None


async def _ensure_consumer_group(redis: Redis) -> None:
    """Creates the Redis Stream consumer group if it does not already exist.

    Retries up to 3 times with backoff. On persistent failure (e.g. ElastiCache
    connection timeout at startup), logs a warning and returns — the consumer
    loop will create the group when Redis recovers. Never crashes the service.
    """
    for attempt in range(3):
        try:
            await redis.xgroup_create(_STREAM_KEY, _CONSUMER_GROUP, id="0", mkstream=True)
            logger.info("audit_consumer_group_created", group=_CONSUMER_GROUP)
            return
        except Exception as exc:
            if "BUSYGROUP" in str(exc):
                logger.debug("audit_consumer_group_already_exists", group=_CONSUMER_GROUP)
                return
            if attempt < 2:
                logger.warning(
                    "audit_consumer_group_retry",
                    error=str(exc),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(2 ** attempt)
            else:
                logger.warning(
                    "audit_consumer_group_deferred",
                    error=str(exc),
                    note="consumer loop will create group when Redis recovers",
                )


async def _process_pending(redis: Redis) -> None:
    """Process any messages that were delivered but not ACKed (e.g. from a crash)."""
    try:
        pending = await redis.xpending_range(
            _STREAM_KEY, _CONSUMER_GROUP, "-", "+", count=100
        )
        if not pending:
            return

        logger.info("audit_processing_pending", count=len(pending))
        ids = [entry["message_id"] for entry in pending]
        messages = await redis.xclaim(
            _STREAM_KEY,
            _CONSUMER_GROUP,
            _CONSUMER_NAME,
            min_idle_time=0,
            message_ids=ids,
        )
        async with SessionLocal() as db:
            for _, fields in messages:
                # 2026-05-13: _parse_stream_event returns (AuditLogCreate, billing_dict).
                # Previously we passed the tuple to AuditWriter.log() which silently
                # raised on attribute access, the message was ACKed, and the row was
                # lost. This created legitimate-looking chain gaps on every crash
                # recovery. Unpack explicitly and pass the typed payload.
                event, billing_data = _parse_stream_event(fields)
                if event:
                    try:
                        # Log is idempotent now with ON CONFLICT
                        await AuditWriter.log(db, redis, event, billing_data=billing_data)
                    except Exception as exc:
                        logger.error("audit_pending_write_failed", error=str(exc))
        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, *ids)
    except Exception as exc:
        logger.warning("audit_pending_check_failed", error=str(exc))


async def _check_backpressure(redis: Redis) -> None:
    """Check for consumer lag and stream length, logging warnings if high."""
    try:
        from sdk.utils import AUDIT_STREAM_LENGTH

        groups = await redis.xinfo_groups(_STREAM_KEY)
        for g in groups:
            if g["name"] == _CONSUMER_GROUP:
                lag = g.get("lag", 0)
                if lag and lag > 1000:
                    logger.warning("audit_consumer_lag_detected", lag=lag)

        # H-10 (2026-05-13): expose stream length so silent MAXLEN trimming is
        # observable instead of invisible.
        stream_len = await redis.xlen(_STREAM_KEY)
        AUDIT_STREAM_LENGTH.set(stream_len)
        # 80% of MAXLEN=50_000 triggers warning; 90% is critical.
        if stream_len > 40_000:
            logger.warning(
                "audit_stream_high_watermark", length=stream_len, threshold=40_000,
            )
        if stream_len > 45_000:
            logger.critical(
                "audit_loss_risk_detected", length=stream_len, threshold=45_000,
            )
    except Exception as exc:
        logger.warning("backpressure_check_failed", error=str(exc))


async def _stream_consumer_loop(redis: Redis) -> None:
    """
    Enterprise-grade background consumer using Redis Consumer Groups.
    """
    logger.info("audit_worker_started", group=_CONSUMER_GROUP, consumer=_CONSUMER_NAME)

    # Process any unacked messages from previous run (crash recovery)
    await _process_pending(redis)

    while True:
        try:
            # Read next batch from stream via consumer group
            messages = await redis.xreadgroup(
                groupname=_CONSUMER_GROUP,
                consumername=_CONSUMER_NAME,
                streams={_STREAM_KEY: ">"},
                count=_BATCH_SIZE,
                block=_BLOCK_MS
            )

            if not messages:
                continue

            # result = [(stream_key, [(msg_id, fields), ...])]
            for _, batch in messages:
                # PE-8 FIX: Share a single DB session across the full batch so the
                # connection pool is not re-acquired for every individual event.
                async with SessionLocal() as db:
                    to_ack: list[bytes | str] = []
                    for event_id, fields in batch:
                        try:
                            event, billing_data = _parse_stream_event(fields)
                            if not event:
                                raise ValueError("Parse failed — skipping to DLQ")

                            # H-4 FIX (2026-05-13): No external prev/event_hash computation.
                            # AuditWriter.log takes the advisory lock per (tenant, shard),
                            # reads the authoritative prev_hash from DB, computes the hash,
                            # and inserts atomically. Redis is no longer in the chain path.
                            await AuditWriter.log(db, redis, event, billing_data=billing_data)

                            to_ack.append(event_id)

                        except Exception as exc:
                            logger.error("audit_worker_event_failed", event_id=event_id, error=str(exc))
                            # Dead-letter queue for terminal failures; still ACK to advance
                            await redis.xadd(_DLQ_KEY, {
                                "identity": str(event_id),
                                "payload": json.dumps({
                                    k.decode() if isinstance(k, bytes) else k:
                                    v.decode() if isinstance(v, bytes) else v
                                    for k, v in fields.items()
                                }),
                                "error": str(exc),
                                "ts": str(time.time()),
                            })
                            to_ack.append(event_id)

                    # Batch-acknowledge all processed messages in a single XACK call
                    if to_ack:
                        await redis.xack(_STREAM_KEY, _CONSUMER_GROUP, *to_ack)

        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("audit_worker_loop_error", error=str(exc))
            await asyncio.sleep(_RETRY_SLEEP)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables, start stream consumer, clean up on shutdown."""
    # 1. Validate DB schema before accepting traffic
    async with get_session_factory()() as db:
        await check_schema(db, "audit")

    # 2. Connect Redis and ensure consumer group
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    await _ensure_consumer_group(redis)

    # 2. Start consumer background task
    consumer_task = asyncio.create_task(_stream_consumer_loop(redis))

    # H-10 (2026-05-13): periodic backpressure check (was previously dead code)
    async def _bp_loop() -> None:
        while True:
            try:
                await _check_backpressure(redis)
            except asyncio.CancelledError:
                break
            except Exception as _exc:
                logger.warning("backpressure_loop_error", error=str(_exc))
            await asyncio.sleep(10)

    bp_task = asyncio.create_task(_bp_loop())

    # 2026-05-14 — Transactional Outbox drain worker. Backstops the sync
    # billing path so any audit row that didn't get a usage_record via the
    # gateway middleware (network blip, OOM, retries exhausted) is eventually
    # forwarded to /usage/record. Idempotent: usage_records.audit_id is UNIQUE.
    outbox_task = asyncio.create_task(run_outbox_worker())

    # 2026-05-14 — Daily Merkle transparency log scheduler. Commits one root
    # per (tenant, day) for every complete day in the backfill window. Safe to
    # restart: idempotent upserts. See services/audit/transparency_scheduler.py.
    from services.audit.transparency_scheduler import run_transparency_scheduler
    transparency_task = asyncio.create_task(
        run_transparency_scheduler(get_session_factory())
    )

    # 2026-05-26 — Scheduled report email delivery worker. Polls Redis for
    # acp:report_trigger:* keys written by trigger_report_now() and emails
    # the generated PDF to each report's recipients via SMTP.
    from services.audit.report_delivery import run_report_delivery_worker
    delivery_task = asyncio.create_task(
        run_report_delivery_worker(get_session_factory())
    )

    # 2026-06-13 — Sprint 5 — Attack Evaluation Suite runner.
    # Polls eval_jobs for queued runs, replays each dataset case through
    # the REAL gateway /execute, scores results, snapshots per-rule trend.
    # Gated on AEGIS_EVAL_USER+AEGIS_EVAL_PASSWORD being set; otherwise
    # the worker logs a warning and stays idle so the rest of the audit
    # service still boots in dev environments without eval credentials.
    eval_task = None
    if os.getenv("AEGIS_EVAL_USER") and os.getenv("AEGIS_EVAL_PASSWORD"):
        from services.audit.evaluation_runner import run_forever as _eval_run_forever
        eval_task = asyncio.create_task(_eval_run_forever())
    else:
        logger.info(
            "eval_runner_disabled",
            reason="AEGIS_EVAL_USER / AEGIS_EVAL_PASSWORD not set",
        )

    # 2026-06-13 — Sprint 6 — Online evaluation worker (shadow-mode drift).
    # Polls per-tenant configs, scores recent shadow_decisions rows, writes
    # snapshots and fires a notification when FP rate crosses threshold.
    # Always-on (no creds required) since it only reads DB rows the gateway
    # has already written. If no tenant has an online_eval_configs row it
    # exits the inner loop in microseconds and goes back to sleep.
    from services.audit.online_eval_worker import run_forever as _online_eval_run_forever
    online_eval_task = asyncio.create_task(_online_eval_run_forever())

    logger.info("audit_service_started")
    yield

    # 3. Graceful shutdown with durability guarantee
    logger.info("audit_service_shutting_down_gracefully")

    # Signal consumer to stop (will catch CancelledError)
    consumer_task.cancel()
    bp_task.cancel()
    outbox_task.cancel()
    transparency_task.cancel()
    delivery_task.cancel()
    if eval_task is not None:
        eval_task.cancel()
    online_eval_task.cancel()

    # Wait for consumer to finish processing current batch
    try:
        await asyncio.wait_for(consumer_task, timeout=10.0)
    except TimeoutError:
        logger.warning("audit_consumer_shutdown_timeout")
    except asyncio.CancelledError:
        pass

    # Wait for outbox worker to finish its current batch (10s grace).
    try:
        await asyncio.wait_for(outbox_task, timeout=10.0)
    except TimeoutError:
        logger.warning("audit_outbox_shutdown_timeout")
    except asyncio.CancelledError:
        pass

    # Transparency scheduler is just a sleep loop — should exit immediately.
    with suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(transparency_task, timeout=5.0)

    # Report delivery worker is also a sleep loop — exit immediately.
    with suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(delivery_task, timeout=5.0)

    # Final cleanup
    await redis.aclose()
    await engine.dispose()
    logger.info("audit_service_stopped")


app = FastAPI(
    title="ACP Audit Service",
    description="Centralized immutable logging for agent actions — Stream consumer",
    version="2.0.0",
    lifespan=lifespan,
)

# Consolidated SDK Setup
setup_app(app, "audit")

app.include_router(router)
app.include_router(pending_router)

# Sprint 4 — Fleet dashboard endpoints (KPIs, time-series, agent-health, recent-events)
from services.audit.fleet_router import fleet_router  # noqa: E402

app.include_router(fleet_router)

from services.audit.reports import router as reports_router  # noqa: E402

app.include_router(reports_router)

# Daily Merkle transparency log — root commitment over signed receipts.
from services.audit.transparency import transparency_router  # noqa: E402

app.include_router(transparency_router)

# Compliance evidence engine — EU AI Act / NIST AI RMF / SOC 2 / tool ledger.
from services.audit.compliance import compliance_router  # noqa: E402

app.include_router(compliance_router)

# Audit log CSV/JSON export — POST /audit/export
from services.audit.compliance import audit_export_router  # noqa: E402

app.include_router(audit_export_router)

# In-app notifications
from services.audit.compliance import _notifications_router  # noqa: E402

app.include_router(_notifications_router)

# Incident workflow — PATCH status + comment thread
from services.audit.compliance import incidents_router  # noqa: E402

app.include_router(incidents_router)

# Sprint 5 — Attack Evaluation Suite: datasets, evaluators, jobs, efficacy.
from services.audit.evaluation_router import evaluation_router  # noqa: E402

app.include_router(evaluation_router)

# Sprint 6 — Shadow-mode policies + online evaluation.
from services.audit.shadow_router import shadow_router  # noqa: E402

app.include_router(shadow_router)

# Sprint 7 — Policy Playground (validate, replay, publish).
from services.audit.playground_router import playground_router  # noqa: E402

app.include_router(playground_router)


# ── Receipt key endpoint (top-level, not under /logs) ───────────────────
@app.get("/receipts/key", tags=["receipts"])
async def get_signing_public_key() -> dict[str, object]:
    """Return the ed25519 public key used to sign execution receipts.

    Customers/auditors fetch this once, cache it, and use it to verify any
    number of receipts offline via the SDK's `verify_receipt(...)`.
    """
    from services.audit.signer import get_signer
    return get_signer().public_key_info()


@app.get("/transparency/key", tags=["transparency"])
async def get_root_signing_public_key() -> dict[str, object]:
    """Return the ed25519 public key used to sign daily transparency roots.

    Separate from /receipts/key so the receipt-signing key can rotate
    independently of historical-root signatures. If no separate root key is
    configured, this returns the same key as /receipts/key — the fingerprint
    field in either response is authoritative for which key signed a
    specific payload.
    """
    from services.audit.signer import get_root_signer
    return get_root_signer().public_key_info()


# ── Crypto verify endpoint — server-side proof for auditors who can't run SDK ──
@app.post("/receipts/verify", tags=["receipts"])
async def verify_signed_receipt(
    payload: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, object]:
    """Verify a signed execution receipt server-side.

    Body shape (matches what `/logs/{exec_id}/receipt` returns under `data`):
        { receipt: {...}, signature: "<b64>",
          algorithm: "ed25519", public_key_fingerprint: "<hex32>" }

    Tries the active receipt-signing key first; on fingerprint mismatch
    consults `transparency_historical_keys` so receipts signed before a key
    rotation still verify with `valid: true`. The returned
    `expected_fingerprint` identifies which key actually validated the
    payload — clients can show that alongside `valid: true` to communicate
    "this was signed by your previous key, which we still recognise."
    """
    from services.audit.signer import (
        get_signer,
        verify_receipt_against_known_keys,
    )
    active = get_signer()
    try:
        ok, used_fp = await verify_receipt_against_known_keys(db, payload)
    except ValueError as exc:
        return {
            "valid":                False,
            "algorithm":            "ed25519",
            "expected_fingerprint": active._fingerprint,  # noqa: SLF001
            "errors":               ["malformed_payload"],
            "reason":               str(exc),
        }
    if ok:
        return {
            "valid":                True,
            "algorithm":            "ed25519",
            "expected_fingerprint": used_fp or active._fingerprint,  # noqa: SLF001
            "errors":               [],
        }
    return {
        "valid":                False,
        "algorithm":            "ed25519",
        "expected_fingerprint": active._fingerprint,  # noqa: SLF001
        "errors":               ["signature_mismatch"],
    }


# ── Board-level executive PDF report ────────────────────────────────────────

@app.post("/board-report", tags=["compliance"])
async def board_report(
    payload: dict,
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id_dep: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> Any:
    """
    Generate a board-level executive PDF security report.

    Body: {"start_date": "...", "end_date": "...", "tenant_id": "..."}

    Returns application/pdf with Content-Disposition attachment.
    """
    from datetime import UTC, datetime, timedelta

    from fastapi.responses import Response
    from sqlalchemy import func, select

    from services.audit.models import AuditLog

    # ── Parse request body ──────────────────────────────────────────────────
    now_utc = datetime.now(UTC)
    default_end = now_utc
    default_start = now_utc - timedelta(days=30)

    def _parse_dt(s: str | None, default: datetime) -> datetime:
        if not s:
            return default
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail=f"Invalid date format '{s}'. Expected ISO-8601.",
            )

    start_str = payload.get("start_date")
    end_str = payload.get("end_date")
    period_start = _parse_dt(start_str, default_start)
    period_end = _parse_dt(end_str, default_end)

    # Allow tenant_id override in body (gateway injects via header as well)
    body_tenant_id = payload.get("tenant_id")
    try:
        tenant_id = uuid.UUID(body_tenant_id) if body_tenant_id else tenant_id_dep
    except (ValueError, AttributeError):
        tenant_id = tenant_id_dep

    start_display = period_start.strftime("%Y-%m-%d")
    end_display = period_end.strftime("%Y-%m-%d")

    # ── Query audit_logs for summary ────────────────────────────────────────
    total_q = await db.execute(
        select(func.count()).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= period_start,
            AuditLog.created_at <= period_end,
        )
    )
    total = total_q.scalar() or 0

    blocked_q = await db.execute(
        select(func.count()).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= period_start,
            AuditLog.created_at <= period_end,
            AuditLog.decision.in_(["deny", "block", "kill"]),
        )
    )
    blocked = blocked_q.scalar() or 0
    allowed = total - blocked
    block_rate = (blocked / total * 100) if total > 0 else 0.0

    summary = {
        "total": total,
        "allowed": allowed,
        "blocked": blocked,
        "block_rate": block_rate,
        "incidents_resolved": 0,
        "policy_compliance_pct": 100.0,
        "chain_integrity_pct": 100.0,
        "avg_response_ms": 0,
    }

    # ── Top blocked tools ───────────────────────────────────────────────────
    top_tools_q = await db.execute(
        select(AuditLog.tool, func.count().label("cnt"))
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.created_at >= period_start,
            AuditLog.created_at <= period_end,
            AuditLog.decision.in_(["deny", "block", "kill"]),
            AuditLog.tool.isnot(None),
        )
        .group_by(AuditLog.tool)
        .order_by(func.count().desc())
        .limit(10)
    )
    top_tools = [
        {"tool_name": row.tool, "count": row.cnt}
        for row in top_tools_q.fetchall()
    ]

    # ── Generate PDF ────────────────────────────────────────────────────────
    try:
        from services.audit.board_report import generate_board_report_pdf
    except ImportError as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=501,
            detail=(
                "PDF export requires reportlab which is not installed. "
                f"Error: {exc}"
            ),
        ) from exc

    try:
        pdf_bytes = generate_board_report_pdf(
            tenant_id=str(tenant_id),
            start_date=start_display,
            end_date=end_display,
            summary=summary,
            incidents=[],
            top_tools=top_tools,
        )
    except Exception as exc:
        logger.error("board_report_pdf_generation_failed", error=str(exc))
        from fastapi import HTTPException
        raise HTTPException(
            status_code=500,
            detail=f"Board report generation failed: {type(exc).__name__}: {exc}",
        ) from exc

    date_slug = now_utc.strftime("%Y%m%d")
    filename = f"board-report-{date_slug}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
