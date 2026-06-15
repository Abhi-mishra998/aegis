"""Gateway proxy routes for the agent registry.

11 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup. All proxy to the registry service which owns agent CRUD,
permissions, and behavioural profiles:

  /agents               — list / create
  /agents/summary       — fleet counts by status + high-risk
  /registry/tools       — deduplicated tool name list
  /agents/{id}          — get / update / delete
  /agents/{id}/profile  — behavioural profile
  /agents/{id}/permissions       — list / add
  /agents/{id}/permissions/{pid} — revoke

The POST /agents and DELETE /agents/{id} handlers fan out
``agent_created`` / ``agent_deleted`` SSE events to the per-tenant +
per-agent channels.

POST /agents/{id}/permissions has client-payload normalisation so
callers can send ``allowed: bool`` instead of ``action: ALLOW|DENY``,
plus auto-inject of ``granted_by`` from the JWT-authenticated role.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    publish_event,
    trust_proxy,
)

router = APIRouter()

_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _base() -> str:
    return settings.REGISTRY_SERVICE_URL.rstrip("/")


# ── List + create + summary + tools ──────────────────────────────────────

@router.get("/agents", tags=["agents"])
async def list_agents(request: Request) -> Any:
    """Proxy → Registry service list agents."""
    resp = await request.app.state.client.get(
        f"{_base()}/agents",
        params=request.query_params,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/agents", tags=["agents"])
async def create_agent(request: Request, response: Response) -> Any:
    """Proxy → Registry service create agent. Publishes agent_created SSE event.

    Ties ``owner_id`` to the JWT-authenticated actor (M-12 fix) so
    audit + ownership trails carry the real user_id, not whatever the
    caller chose to put in the body.
    """
    body = await request.json()
    body = dict(body)

    actor = getattr(request.state, "actor", "unknown")
    if actor and actor != "unknown":
        body["owner_id"] = actor

    resp = await request.app.state.client.post(
        f"{_base()}/agents",
        json=body,
        headers=internal_headers(request),
    )
    response.status_code = resp.status_code
    result = resp.json()
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 201):
        agent_data = result.get("data", result) if isinstance(result, dict) else {}
        new_agent_id = str(agent_data.get("id", "")) if isinstance(agent_data, dict) else ""
        await publish_event(
            _redis, tenant_id_str, "agent_created", agent_data,
            agent_id=new_agent_id or None,
        )
    return result


# Specific paths /agents/summary + /registry/tools must precede the
# catch-all /agents/{agent_id} so FastAPI doesn't match "summary" or
# "tools" as an agent_id.

@router.get("/agents/summary", tags=["agents"])
async def agents_summary(request: Request) -> Any:
    """Proxy → Registry fleet summary (count by status + high-risk count)."""
    return await trust_proxy(settings.REGISTRY_SERVICE_URL, "/agents/summary", request)


@router.get("/registry/tools", tags=["registry"])
async def registry_tools(request: Request) -> Any:
    """Proxy → Registry: deduplicated tool names across all registered agents."""
    return await trust_proxy(settings.REGISTRY_SERVICE_URL, "/agents/tools", request)


# ── Per-agent reads + mutations ──────────────────────────────────────────

# /agents/{id}/profile and /agents/{id}/permissions sit BEFORE the bare
# /agents/{id} so FastAPI doesn't match "profile" / "permissions" /
# nested permission IDs as an agent_id.

@router.get("/agents/{agent_id}/profile", tags=["agents"])
async def agent_profile(agent_id: str, request: Request) -> Any:
    """Proxy → Registry agent behavioral profile."""
    return await trust_proxy(settings.REGISTRY_SERVICE_URL, f"/agents/{agent_id}/profile", request)


@router.get("/agents/{agent_id}/permissions", tags=["agents"])
async def list_agent_permissions(agent_id: str, request: Request) -> Any:
    """Proxy → Registry list agent permissions."""
    resp = await request.app.state.client.get(
        f"{_base()}/agents/{agent_id}/permissions",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/agents/{agent_id}/permissions", tags=["agents"])
async def add_agent_permission(agent_id: str, request: Request, response: Response) -> Any:
    """Proxy → Registry add agent permission.

    Client-payload normalisation:
      * maps the convenience ``allowed: bool`` field to ``action: ALLOW|DENY``
      * injects ``granted_by`` from the JWT-authenticated role when absent
    """
    body = await request.json()
    body = dict(body)

    if "action" not in body and "allowed" in body:
        body["action"] = "ALLOW" if body.pop("allowed") else "DENY"
    body.pop("allowed", None)

    if not body.get("granted_by"):
        role = getattr(request.state, "role", None)
        body["granted_by"] = str(role) if role else "system"

    resp = await request.app.state.client.post(
        f"{_base()}/agents/{agent_id}/permissions",
        json=body,
        headers=internal_headers(request),
    )
    response.status_code = resp.status_code
    return passthrough(resp)


@router.delete("/agents/{agent_id}/permissions/{permission_id}", tags=["agents"])
async def revoke_agent_permission(agent_id: str, permission_id: str, request: Request) -> Any:
    """Proxy → Registry revoke agent permission."""
    resp = await request.app.state.client.delete(
        f"{_base()}/agents/{agent_id}/permissions/{permission_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/agents/{agent_id}", tags=["agents"])
async def get_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry get single agent."""
    resp = await request.app.state.client.get(
        f"{_base()}/agents/{agent_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.patch("/agents/{agent_id}", tags=["agents"])
async def update_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry update agent."""
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{_base()}/agents/{agent_id}",
        json=body,
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.delete("/agents/{agent_id}", tags=["agents"])
async def delete_agent(agent_id: str, request: Request) -> Any:
    """Proxy → Registry delete agent. Publishes agent_deleted SSE event."""
    resp = await request.app.state.client.delete(
        f"{_base()}/agents/{agent_id}",
        headers=internal_headers(request),
    )
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 204):
        await publish_event(
            _redis, tenant_id_str, "agent_deleted", {"agent_id": agent_id},
            agent_id=agent_id,
        )
    return passthrough(resp)


# Sprint B 2026-06-14 — quarantine routes (blast radius).
@router.post("/agents/{agent_id}/quarantine", tags=["agents"])
async def quarantine_agent_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Registry: quarantine an agent.

    The body is optional `{reason: str}`. The registry handler sets the
    Redis flag the gateway middleware short-circuits on AND flips the
    persistent status. Publishes an SSE event so the Fleet UI lights up.
    """
    try:
        body = await request.json() if request.headers.get("content-length") else {}
    except Exception:
        body = {}
    resp = await request.app.state.client.post(
        f"{_base()}/agents/{agent_id}/quarantine",
        json=body, headers=internal_headers(request),
    )
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 201):
        await publish_event(
            _redis, tenant_id_str, "agent_quarantined",
            {"agent_id": agent_id, "reason": body.get("reason", "manual")},
            agent_id=agent_id,
        )
    return passthrough(resp)


@router.delete("/agents/{agent_id}/quarantine", tags=["agents"])
async def release_quarantine_proxy(agent_id: str, request: Request) -> Any:
    """Proxy → Registry: release agent from quarantine."""
    resp = await request.app.state.client.delete(
        f"{_base()}/agents/{agent_id}/quarantine",
        headers=internal_headers(request),
    )
    tenant_id_str = request.headers.get("X-Tenant-ID", "")
    if tenant_id_str and resp.status_code in (200, 204):
        await publish_event(
            _redis, tenant_id_str, "agent_quarantine_released",
            {"agent_id": agent_id}, agent_id=agent_id,
        )
    return passthrough(resp)
