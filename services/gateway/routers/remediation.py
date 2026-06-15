"""Sprint 6 — Auto-Remediation read + control API.

Routes:

  GET  /remediation/incidents/{incident_id}
       Ledger view — every action fired for one incident.

  POST /remediation/incidents/{incident_id}/replay
       Force re-run for an incident (e.g. after a transient webhook
       failure). Idempotency markers are bypassed; new ledger rows are
       appended.

  GET  /remediation/policy
       Read the tenant's RemediationPolicy.

  PUT  /remediation/policy
       Replace the tenant's RemediationPolicy.

  POST /remediation/dry-run
       Returns the action set the executor *would* fire for a synthetic
       (agent_id, incident_id) pair given the current policy. No Redis
       writes, no webhook calls. Useful for previewing policy changes.

All routes require the standard tenant JWT (gateway auth enforces it).
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.security.incidents import store as incident_store
from services.security.remediation import executor, policy as policy_mod

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _tenant_id(request: Request) -> str:
    tid = getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", "")
    if not tid:
        raise HTTPException(status_code=401, detail="tenant_id missing on request")
    return str(tid)


@router.get("/remediation/incidents/{incident_id}", tags=["Remediation"])
async def get_remediation_ledger(incident_id: str, request: Request) -> Any:
    """Return the chronological action ledger for one incident."""
    tenant_id = _tenant_id(request)
    if not incident_id.startswith("INC-"):
        raise HTTPException(status_code=400, detail="incident_id must look like INC-…")
    ledger = await executor.get_ledger(_redis, tenant_id, incident_id)
    return {
        "incident_id": incident_id,
        "items":       [a.to_dict() for a in ledger],
        "count":       len(ledger),
    }


@router.post("/remediation/incidents/{incident_id}/replay", tags=["Remediation"])
async def replay_remediation(incident_id: str, request: Request) -> Any:
    """Force re-run for one incident. Appends fresh ledger rows."""
    tenant_id = _tenant_id(request)
    if not incident_id.startswith("INC-"):
        raise HTTPException(status_code=400, detail="incident_id must look like INC-…")
    storyline = await incident_store.get(_redis, tenant_id, incident_id)
    if storyline is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    agent_ids = list(storyline.participating_agents)
    if not agent_ids:
        raise HTTPException(
            status_code=409,
            detail=f"incident {incident_id} has no participating agents — no agent to remediate",
        )
    httpx_client = getattr(request.app.state, "client", None) or httpx.AsyncClient(timeout=10.0)
    new_actions = await executor.replay(
        _redis,
        incident_id=incident_id,
        tenant_id=tenant_id,
        agent_id=agent_ids[0],
        storyline=storyline.to_dict(),
        httpx_client=httpx_client,
    )
    return {
        "incident_id":  incident_id,
        "new_actions":  [a.to_dict() for a in new_actions],
        "count":        len(new_actions),
    }


@router.get("/remediation/policy", tags=["Remediation"])
async def get_remediation_policy(request: Request) -> Any:
    tenant_id = _tenant_id(request)
    p = await policy_mod.policy_for_tenant(_redis, tenant_id)
    return p.to_dict()


@router.put("/remediation/policy", tags=["Remediation"])
async def put_remediation_policy(request: Request) -> Any:
    tenant_id = _tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="request body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    default = policy_mod.DEFAULT_POLICY
    new_policy = policy_mod.RemediationPolicy(
        revoke_api_keys=bool(body.get("revoke_api_keys", default.revoke_api_keys)),
        kill_active_tokens=bool(body.get("kill_active_tokens", default.kill_active_tokens)),
        page_oncall=bool(body.get("page_oncall", default.page_oncall)),
        audit_log=bool(body.get("audit_log", default.audit_log)),
        webhook_url=str(body.get("webhook_url", default.webhook_url) or ""),
    )
    await policy_mod.upsert_policy(_redis, tenant_id, new_policy)
    return new_policy.to_dict()


@router.post("/remediation/dry-run", tags=["Remediation"])
async def dry_run(request: Request) -> Any:
    """Preview the action set the executor would fire given the current
    policy. No Redis writes, no webhook calls — safe to call repeatedly."""
    tenant_id = _tenant_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    incident_id = str(body.get("incident_id") or "INC-dry-run")
    agent_id    = str(body.get("agent_id") or "agent-dry-run")
    p = await policy_mod.policy_for_tenant(_redis, tenant_id)
    actions = await executor.execute(
        _redis,
        incident_id=incident_id, tenant_id=tenant_id, agent_id=agent_id,
        policy=p, dry_run=True,
    )
    return {
        "policy":  p.to_dict(),
        "actions": [a.to_dict() for a in actions],
        "count":   len(actions),
    }
