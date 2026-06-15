"""
Flight Recorder — stream consumer.
Subscribes to acp:flight_events; persists timelines/steps/snapshots/artifacts.
The gateway is the canonical producer (see gateway.middleware._record_flight_step).

Event shapes (one JSON object per stream entry, in field 'data'):

  {"kind": "timeline_start", "tenant_id": ..., "request_id": ..., "agent_id": ..., "tool": ..., "metadata": {...}}
  {"kind": "step",           "tenant_id": ..., "request_id": ..., "step_index": 0, "step_type": "policy", "summary": "...", "payload": {...}, "latency_ms": 4, "risk_score": 0.2}
  {"kind": "snapshot",       "tenant_id": ..., "request_id": ..., "step_index": 3, "snapshot": {...}, "tokens_in": 510, "tokens_out": 220}
  {"kind": "artifact",       "tenant_id": ..., "request_id": ..., "step_index": 1, "kind_": "prompt", "sha256": "...", "content": "..."}
  {"kind": "timeline_end",   "tenant_id": ..., "request_id": ..., "final_decision": "allow", "final_risk": 0.13, "status": "ok"}

The worker is idempotent: every step is keyed by (timeline_id, step_index),
duplicates are silently dropped (ON CONFLICT DO NOTHING).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from services.flight_recorder.models import (
    ExecutionArtifact,
    ExecutionSnapshot,
    ExecutionStep,
    ExecutionTimeline,
)

logger = structlog.get_logger(__name__)

FLIGHT_STREAM_KEY     = "acp:flight_events"
FLIGHT_CONSUMER_GROUP = "acp:flight_recorder"
FLIGHT_CONSUMER_NAME  = os.getenv("FLIGHT_CONSUMER_NAME", "flight-recorder-1")
FLIGHT_DLQ_KEY        = "acp:flight_events:dlq"
BLOCK_MS              = 2000
BATCH_SIZE            = 100
RETRY_SLEEP_S         = 2.0


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(FLIGHT_STREAM_KEY, FLIGHT_CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _get_or_create_timeline(
    db, tenant_id: uuid.UUID, request_id: str, ev: dict[str, Any]
) -> ExecutionTimeline:
    existing = (await db.execute(
        select(ExecutionTimeline).where(
            ExecutionTimeline.tenant_id == tenant_id,
            ExecutionTimeline.request_id == request_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    agent_id = ev.get("agent_id")
    try:
        agent_uuid = uuid.UUID(agent_id) if agent_id else None
    except Exception:
        agent_uuid = None
    t = ExecutionTimeline(
        tenant_id=tenant_id, org_id=tenant_id,
        request_id=request_id, agent_id=agent_uuid,
        tool=ev.get("tool"),
        session_id=(ev.get("session_id") or None),  # Sprint 3.5
        metadata_json=ev.get("metadata") or {},
        status="in_progress",
    )
    db.add(t)
    await db.commit()
    return t


async def _apply_event(db, ev: dict[str, Any]) -> None:
    tenant_id_s = ev.get("tenant_id")
    request_id = ev.get("request_id")
    if not tenant_id_s or not request_id:
        return
    try:
        tenant_id = uuid.UUID(tenant_id_s)
    except Exception:
        return

    kind = ev.get("kind")
    timeline = await _get_or_create_timeline(db, tenant_id, request_id, ev)

    if kind == "timeline_start":
        # Out-of-order delivery is normal: emit_step (steps 0-2) is scheduled
        # before emit_timeline_start in the gateway, and Redis Streams + the
        # asyncio scheduler don't guarantee FIFO. If a step landed first, the
        # row was created with tool=None/agent_id=None — backfill those fields
        # now from the canonical timeline_start payload. This is the only place
        # in the flight pipeline that owns those columns, so the update is
        # idempotent: subsequent timeline_start replays leave the row alone.
        dirty = False
        ev_tool = ev.get("tool")
        if ev_tool and not timeline.tool:
            timeline.tool = ev_tool
            dirty = True
        ev_agent = ev.get("agent_id")
        if ev_agent and timeline.agent_id is None:
            try:
                timeline.agent_id = uuid.UUID(ev_agent)
                dirty = True
            except (ValueError, TypeError):
                pass
        # Sprint 3.5 — backfill session_id when timeline_start lands after
        # a step event has already created the row.
        ev_session = ev.get("session_id")
        if ev_session and not timeline.session_id:
            timeline.session_id = ev_session
            dirty = True
        ev_meta = ev.get("metadata") or {}
        if ev_meta and not (timeline.metadata_json or {}):
            timeline.metadata_json = ev_meta
            dirty = True
        if dirty:
            await db.commit()
        return
    if kind == "step":
        step_index = int(ev.get("step_index", 0))
        stmt = insert(ExecutionStep).values(
            tenant_id=tenant_id, org_id=tenant_id,
            timeline_id=timeline.id, request_id=request_id,
            step_index=step_index, step_type=ev.get("step_type", "policy"),
            status=ev.get("status", "ok"),
            latency_ms=ev.get("latency_ms"),
            risk_score=ev.get("risk_score"),
            summary=ev.get("summary"),
            payload=ev.get("payload") or {},
        ).on_conflict_do_nothing(index_elements=["id"])
        await db.execute(stmt)
        await db.commit()
        return
    if kind == "snapshot":
        snap = ExecutionSnapshot(
            tenant_id=tenant_id, org_id=tenant_id,
            timeline_id=timeline.id,
            step_index=int(ev.get("step_index", 0)),
            snapshot=ev.get("snapshot") or {},
            tokens_in=ev.get("tokens_in"),
            tokens_out=ev.get("tokens_out"),
        )
        db.add(snap)
        await db.commit()
        return
    if kind == "artifact":
        content = ev.get("content") or ""
        sha = ev.get("sha256") or hashlib.sha256(content.encode()).hexdigest()
        art = ExecutionArtifact(
            tenant_id=tenant_id, org_id=tenant_id,
            timeline_id=timeline.id,
            kind=ev.get("kind_", "prompt"),
            sha256=sha,
            size_bytes=len(content),
            content=content[:32_000],  # cap to 32 KB inline; producer should externalize larger
        )
        db.add(art)
        await db.commit()
        return
    if kind == "timeline_end":
        timeline.completed_at = datetime.now(tz=UTC)
        if timeline.started_at:
            timeline.duration_ms = int(
                (timeline.completed_at - timeline.started_at).total_seconds() * 1000
            )
        timeline.final_decision = ev.get("final_decision")
        timeline.final_risk = ev.get("final_risk")
        timeline.status = ev.get("status", "ok")
        await db.commit()
        return


async def _consumer(redis: Redis, session_factory: async_sessionmaker) -> None:
    await _ensure_group(redis)
    while True:
        try:
            messages = await redis.xreadgroup(
                groupname=FLIGHT_CONSUMER_GROUP,
                consumername=FLIGHT_CONSUMER_NAME,
                streams={FLIGHT_STREAM_KEY: ">"},
                count=BATCH_SIZE, block=BLOCK_MS,
            )
            if not messages:
                continue
            async with session_factory() as db:
                to_ack: list[Any] = []
                for _, batch in messages:
                    for msg_id, fields in batch:
                        decoded = {
                            (k.decode() if isinstance(k, bytes) else k):
                            (v.decode() if isinstance(v, bytes) else v)
                            for k, v in fields.items()
                        }
                        try:
                            ev = json.loads(decoded.get("data", "{}"))
                            await _apply_event(db, ev)
                            to_ack.append(msg_id)
                        except Exception as exc:
                            logger.error("flight_event_failed", error=str(exc), id=str(msg_id))
                            await redis.xadd(
                                FLIGHT_DLQ_KEY,
                                {"data": json.dumps(decoded), "error": str(exc)[:200]},
                                maxlen=10_000, approximate=True,
                            )
                            to_ack.append(msg_id)
                if to_ack:
                    await redis.xack(FLIGHT_STREAM_KEY, FLIGHT_CONSUMER_GROUP, *to_ack)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.critical("flight_consumer_loop_error", error=str(exc))
            await asyncio.sleep(RETRY_SLEEP_S)
