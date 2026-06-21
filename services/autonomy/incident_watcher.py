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


# ── Jira auto-create (Sprint EI-2) ───────────────────────────────────────────

def _severity_to_snow_levels(severity: str) -> tuple[int, int]:
    """Map Aegis severity → ServiceNow (urgency, impact) per SNOW's 1=High,
    2=Medium, 3=Low convention. CRITICAL maps to (1,1) i.e. highest priority.
    """
    s = (severity or "HIGH").upper()
    if s == "CRITICAL":  return (1, 1)
    if s == "HIGH":      return (1, 2)
    if s == "MEDIUM":    return (2, 2)
    if s == "LOW":       return (3, 3)
    return (2, 2)  # default to Medium/Medium


async def _maybe_auto_create_jira(
    incident: dict[str, Any], tenant_id: uuid.UUID, session_factory,
) -> None:
    """Open a Jira ticket if the tenant has Jira enabled with auto_create on.

    Best-effort — never raises. Failures are logged but do not block the
    main incident-watcher loop or any downstream playbooks.
    """
    from sqlalchemy import select  # noqa: PLC0415

    try:
        from services.identity.models import JiraIntegration  # noqa: PLC0415
        from services.autonomy.webhook_executor import fire_jira  # noqa: PLC0415
    except Exception as exc:
        logger.warning("jira_auto_import_failed", error=str(exc))
        return

    async with session_factory() as db:
        try:
            res = await db.execute(
                select(JiraIntegration).where(
                    JiraIntegration.tenant_id == tenant_id,
                    JiraIntegration.enabled.is_(True),
                    JiraIntegration.auto_create_on_incident.is_(True),
                ),
            )
            cfg = res.scalar_one_or_none()
        except Exception as exc:
            logger.warning("jira_auto_config_lookup_failed", error=str(exc))
            return

    if cfg is None:
        return

    severity = (incident.get("severity") or "HIGH").upper()
    risk     = incident.get("risk_score")
    inc_id   = incident.get("id", "")
    summary  = f"[Aegis {severity}] {incident.get('trigger') or incident.get('finding') or 'Incident'}"[:255]
    desc     = (
        f"Aegis opened incident {inc_id} (severity={severity}, risk_score={risk}).\n\n"
        f"Tool:    {incident.get('tool', 'n/a')}\n"
        f"Agent:   {incident.get('agent_id', 'n/a')}\n"
        f"Findings: {incident.get('findings', [])}\n\n"
        f"Resolve this ticket once the incident has been triaged in Aegis."
    )

    result = await fire_jira(
        summary=summary,
        base_url=cfg.base_url,
        account_email=cfg.account_email,
        api_token=cfg.api_token,
        project_key=cfg.project_key,
        issue_type=cfg.default_issue_type or "Bug",
        description=desc,
        priority=cfg.default_priority,
        labels=["aegis", f"sev-{severity.lower()}"],
    )
    logger.info(
        "jira_auto_created",
        tenant_id=str(tenant_id)[:8],
        incident_id=inc_id[:8] if inc_id else "",
        outcome=result.get("status"),
        issue_key=result.get("issue_key", ""),
    )

    # Sprint EI-17 — write the issue_key back on the originating Aegis
    # incident so the inbound /webhooks/jira handler can find it later.
    if result.get("status") == "created" and inc_id and result.get("issue_key"):
        await _patch_incident_external_link(
            tenant_id=tenant_id,
            incident_id=inc_id,
            fields={
                "jira_issue_key": result["issue_key"],
                "jira_issue_url": result.get("issue_url", ""),
            },
        )


# ── ServiceNow auto-create (Sprint EI-6) ─────────────────────────────────────

async def _maybe_auto_create_snow(
    incident: dict[str, Any], tenant_id: uuid.UUID, session_factory,
) -> None:
    """Open a ServiceNow Incident if the tenant has SNOW enabled with
    auto_create on. Best-effort; never raises.

    Uses Aegis's incident_id as the SNOW ``correlation_id`` so a retry of
    the same incident does not open a duplicate ticket — SNOW de-dupes
    on correlation_id and returns the existing record.
    """
    from sqlalchemy import select  # noqa: PLC0415

    try:
        from services.identity.models import ServicenowIntegration  # noqa: PLC0415
        from services.autonomy.webhook_executor import fire_servicenow  # noqa: PLC0415
    except Exception as exc:
        logger.warning("snow_auto_import_failed", error=str(exc))
        return

    async with session_factory() as db:
        try:
            res = await db.execute(
                select(ServicenowIntegration).where(
                    ServicenowIntegration.tenant_id == tenant_id,
                    ServicenowIntegration.enabled.is_(True),
                    ServicenowIntegration.auto_create_on_incident.is_(True),
                ),
            )
            cfg = res.scalar_one_or_none()
        except Exception as exc:
            logger.warning("snow_auto_config_lookup_failed", error=str(exc))
            return

    if cfg is None:
        return

    severity = (incident.get("severity") or "HIGH").upper()
    risk     = incident.get("risk_score")
    inc_id   = incident.get("id", "")
    short    = f"[Aegis {severity}] {incident.get('trigger') or incident.get('finding') or 'Incident'}"[:160]
    desc     = (
        f"Aegis opened incident {inc_id} (severity={severity}, risk_score={risk}).\n\n"
        f"Tool:    {incident.get('tool', 'n/a')}\n"
        f"Agent:   {incident.get('agent_id', 'n/a')}\n"
        f"Findings: {incident.get('findings', [])}\n\n"
        f"Resolve this incident once Aegis has been triaged."
    )

    # Prefer per-incident severity → urgency/impact, but let the tenant
    # default override if their on-call rotation expects fixed levels.
    auto_urgency, auto_impact = _severity_to_snow_levels(severity)

    result = await fire_servicenow(
        short_description=short,
        instance_url=cfg.instance_url,
        username=cfg.username,
        password=cfg.password,
        description=desc,
        urgency=auto_urgency,
        impact=auto_impact,
        category=cfg.default_category,
        assignment_group=cfg.default_assignment_group,
        correlation_id=inc_id or None,
    )
    logger.info(
        "snow_auto_created",
        tenant_id=str(tenant_id)[:8],
        incident_id=inc_id[:8] if inc_id else "",
        outcome=result.get("status"),
        number=result.get("number", ""),
    )

    # Sprint EI-17 — write back sys_id + number on the originating
    # incident so /webhooks/servicenow can resolve it on upstream close.
    if result.get("status") == "created" and inc_id and result.get("sys_id"):
        await _patch_incident_external_link(
            tenant_id=tenant_id,
            incident_id=inc_id,
            fields={
                "servicenow_sys_id": result["sys_id"],
                "servicenow_number": result.get("number", ""),
            },
        )


# ── Sprint EI-17 — write-back helper ────────────────────────────────────────

async def _patch_incident_external_link(
    *, tenant_id: uuid.UUID, incident_id: str, fields: dict[str, str],
) -> None:
    """PATCH /incidents/{id} on api-svc to store the external-ticket link.

    Best-effort. Failure here is non-blocking — the ticket WAS created
    in Jira/SNOW; the only downside of a failed link-back is that the
    inbound webhook can't later close the Aegis incident. Logged loudly
    so an operator notices the gap.
    """
    import httpx  # noqa: PLC0415
    import os  # noqa: PLC0415

    api_url = os.environ.get("API_SERVICE_URL", "http://api:8005").rstrip("/")
    internal_secret = os.environ.get("INTERNAL_SECRET", "")
    if not internal_secret:
        logger.warning("incident_link_back_no_secret",
                       reason="INTERNAL_SECRET unset; cannot call api-svc")
        return
    from sdk.common.auth import mesh_headers
    headers = {
        **mesh_headers("autonomy"),
        "X-Tenant-ID":       str(tenant_id),
        "X-ACP-Actor":       "incident_watcher:link-back",
        "Content-Type":      "application/json",
    }
    try:
        # N16 (2026-06-21) — no redirects on internal-cluster calls. If the
        # api-svc ever 301s for an /incidents PATCH it would be a misconfig
        # we want to see, not silently chase to a different host.
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as c:
            r = await c.patch(
                f"{api_url}/incidents/{incident_id}",
                headers=headers,
                json=fields,
            )
        if r.status_code >= 400:
            logger.warning("incident_link_back_failed",
                           http=r.status_code, body=r.text[:160],
                           incident_id=incident_id[:8] if incident_id else "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("incident_link_back_exception", error=str(exc),
                       incident_id=incident_id[:8] if incident_id else "")


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

    # Sprint EI-2 — best-effort Jira ticket creation. Runs in parallel with
    # playbook evaluation so a slow Jira API never blocks playbook firing.
    asyncio.create_task(_maybe_auto_create_jira(incident, tenant_id, session_factory))
    # Sprint EI-6 — same pattern for ServiceNow. Independent task so a slow
    # SNOW instance can't slow down Jira either.
    asyncio.create_task(_maybe_auto_create_snow(incident, tenant_id, session_factory))

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
