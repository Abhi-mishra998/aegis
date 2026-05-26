"""
Autonomy Incident Watcher — auto-triggers playbooks whose trigger_conditions
match incoming incident events from the shared incidents stream.

Consumer group: autonomy-playbook-watcher (separate from api/are consumers)
Stream:         acp:incidents:queue

For each incident the watcher:
  1. Decodes the event payload
  2. Loads all active, auto-mode playbooks for that tenant
  3. Evaluates trigger_conditions against the incident
  4. Fires execute_playbook() in the background for every match
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_INCIDENT_STREAM = "acp:incidents:queue"
_WATCHER_GROUP   = "autonomy-playbook-watcher"
_WATCHER_CONSUMER = "autonomy-watcher-1"
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


# ── Condition evaluator (pure function — no I/O) ──────────────────────────────

def _matches_conditions(incident: dict[str, Any], conditions: dict[str, Any]) -> bool:
    """
    Return True when the incident satisfies ALL entries in conditions.

    Supported matchers:
      risk_score:    {"gte": 0.9} | {"lte": 0.5}
      anomaly_score: {"gte": 0.85}
      severity:      "CRITICAL" | "HIGH" | ...
      tool:          "run_sql"
      finding:       "sql_injection"   (checked against findings list or trigger field)
    """
    if not conditions:
        return False  # empty conditions never auto-fire

    risk = float(incident.get("risk_score", 0) or 0)

    if "risk_score" in conditions:
        spec = conditions["risk_score"]
        if isinstance(spec, dict):
            if "gte" in spec and risk < float(spec["gte"]):
                return False
            if "lte" in spec and risk > float(spec["lte"]):
                return False
        elif risk != float(spec):
            return False

    if "anomaly_score" in conditions:
        spec = conditions["anomaly_score"]
        # use risk_score as proxy when anomaly_score is not a separate field
        anomaly = float(incident.get("anomaly_score", risk) or risk)
        if isinstance(spec, dict):
            if "gte" in spec and anomaly < float(spec["gte"]):
                return False
            if "lte" in spec and anomaly > float(spec["lte"]):
                return False

    if "severity" in conditions:
        if incident.get("severity") != conditions["severity"]:
            return False

    if "tool" in conditions:
        if incident.get("tool") != conditions["tool"]:
            return False

    if "finding" in conditions:
        target = conditions["finding"]
        # findings may be a list or a single string
        findings = incident.get("findings") or []
        if isinstance(findings, str):
            findings = [findings]
        # also check the trigger field as a fallback
        trigger = incident.get("trigger", "")
        if target not in findings and target not in trigger:
            return False

    if "findings_contains" in conditions:
        required = conditions["findings_contains"]
        if isinstance(required, str):
            required = [required]
        findings = incident.get("findings") or []
        if isinstance(findings, str):
            findings = [findings]
        trigger = incident.get("trigger", "")
        combined = set(findings) | ({trigger} if trigger else set())
        if not any(r in combined for r in required):
            return False

    return True


# ── Per-incident handler ──────────────────────────────────────────────────────

async def _handle_incident(incident: dict[str, Any], session_factory) -> None:
    """Load active auto-mode playbooks for this tenant and fire matches."""
    from sqlalchemy import select  # noqa: PLC0415

    from services.autonomy.playbooks import Playbook  # noqa: PLC0415

    raw_tid = incident.get("tenant_id")
    if not raw_tid:
        return

    try:
        tenant_id = uuid.UUID(str(raw_tid))
    except ValueError:
        logger.warning("incident_watcher_bad_tenant", raw=raw_tid)
        return

    async with session_factory() as db:
        stmt = (
            select(Playbook)
            .where(
                Playbook.tenant_id == tenant_id,
                Playbook.is_active.is_(True),
                Playbook.mode == "auto",
            )
        )
        playbooks = (await db.execute(stmt)).scalars().all()

    for pb in playbooks:
        conditions = pb.trigger_conditions or {}
        if not _matches_conditions(incident, conditions):
            continue

        logger.info(
            "playbook_auto_triggered",
            playbook_id=str(pb.id)[:8],
            tenant_id=str(tenant_id)[:8],
            risk_score=incident.get("risk_score"),
        )

        async def _run(pb_id=pb.id, tid=tenant_id) -> None:
            async with session_factory() as bg_db:
                try:
                    from services.autonomy.playbooks import (
                        execute_playbook as _exec,  # noqa: PLC0415
                    )
                    await _exec(
                        playbook_id=pb_id,
                        context={
                            "source":      "incident_watcher",
                            "incident_id": incident.get("id", ""),
                            "agent_id":    incident.get("agent_id", ""),
                            "tenant_id":   str(tid),
                            "risk_score":  incident.get("risk_score", 0),
                            "severity":    incident.get("severity", "HIGH"),
                        },
                        db=bg_db,
                        tenant_id=tid,
                        triggered_by="auto",
                    )
                except Exception as exc:
                    logger.error("playbook_auto_execute_failed",
                                 playbook_id=str(pb_id)[:8], error=str(exc))

        asyncio.create_task(_run())


# ── Background consumer loop ──────────────────────────────────────────────────

async def run_incident_watcher(session_factory) -> None:
    """
    Durable Redis Stream consumer.  Runs as a background task inside the
    autonomy service lifespan and never raises — errors are logged.
    """
    from redis.asyncio import Redis  # noqa: PLC0415

    redis = Redis.from_url(_REDIS_URL, decode_responses=False)

    try:
        await redis.xgroup_create(_INCIDENT_STREAM, _WATCHER_GROUP, id="$", mkstream=True)
    except Exception:
        pass  # group already exists

    logger.info("incident_watcher_started")

    while True:
        try:
            msgs = await redis.xreadgroup(
                _WATCHER_GROUP,
                _WATCHER_CONSUMER,
                {_INCIDENT_STREAM: ">"},
                count=20,
                block=2000,
            )
            for _stream, entries in (msgs or []):
                for msg_id, fields in entries:
                    try:
                        raw  = fields.get(b"data") or fields.get("data") or b"{}"
                        data = json.loads(raw if isinstance(raw, str) else raw.decode())
                        await _handle_incident(data, session_factory)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.error("incident_watcher_event_failed", error=str(exc))
                    finally:
                        await redis.xack(_INCIDENT_STREAM, _WATCHER_GROUP, msg_id)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("incident_watcher_loop_error", error=str(exc))
            await asyncio.sleep(2)

    await redis.aclose()
    logger.info("incident_watcher_stopped")
