"""Gateway proxy routes for the Autonomous Response Engine (ARE).

All 16 ``/auto-response/*`` routes lifted here from services/gateway/main.py
as part of the sprint-5 audit cleanup. Each route is a thin reverse proxy
to the API service that owns the actual ARE logic (rule CRUD, simulation,
metrics, pending-approvals queue, replay).

The routes use ``internal_headers(request)`` to forward the X-Internal-Secret
+ X-Tenant-ID + Authorization + X-Request-ID + X-Trace-ID set the gateway
middleware already authenticated upstream, so the API service receives a
fully-bound identity context.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

router = APIRouter()


def _base() -> str:
    return settings.API_SERVICE_URL.rstrip("/")


# ── ARE Rule CRUD ─────────────────────────────────────────────────────────

@router.post("/auto-response/rules", tags=["ARE"])
async def are_create_rule(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/rules",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/rules", tags=["ARE"])
async def are_list_rules(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/rules",
        headers=internal_headers(request),
    )
    return passthrough(resp)


# /auto-response/rules/{rule_id}/history and /rollback must be declared BEFORE
# the catch-all /auto-response/rules/{rule_id} so FastAPI does not greedily
# match "history" or "rollback" as a rule_id.
@router.get("/auto-response/rules/{rule_id}/history", tags=["ARE"])
async def are_rule_history(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/rules/{rule_id}/history",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auto-response/rules/{rule_id}/rollback/{version}", tags=["ARE"])
async def are_rollback(rule_id: str, version: int, request: Request) -> Any:
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/rules/{rule_id}/rollback/{version}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auto-response/rules/{rule_id}/feedback", tags=["ARE"])
async def are_feedback(rule_id: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/rules/{rule_id}/feedback",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_get_rule(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/rules/{rule_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.patch("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_update_rule(rule_id: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.patch(
        f"{_base()}/auto-response/rules/{rule_id}",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.delete("/auto-response/rules/{rule_id}", tags=["ARE"])
async def are_delete_rule(rule_id: str, request: Request) -> Any:
    resp = await request.app.state.client.delete(
        f"{_base()}/auto-response/rules/{rule_id}",
        headers=internal_headers(request),
    )
    return Response(status_code=resp.status_code)


# ── ARE control plane ─────────────────────────────────────────────────────

@router.post("/auto-response/toggle", tags=["ARE"])
async def are_toggle(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/toggle",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/toggle", tags=["ARE"])
async def are_get_toggle(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/toggle",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auto-response/simulate", tags=["ARE"])
async def are_simulate(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/simulate",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/metrics", tags=["ARE"])
async def are_metrics(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/metrics",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/pending", tags=["ARE"])
async def are_list_pending(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/pending",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auto-response/pending/{approval_key}/approve", tags=["ARE"])
async def are_approve_pending(approval_key: str, request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/pending/{approval_key}/approve",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/auto-response/replay", tags=["ARE"])
async def are_replay(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/auto-response/replay",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/auto-response/latency", tags=["ARE"])
async def are_latency(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/auto-response/latency",
        headers=internal_headers(request),
    )
    return passthrough(resp)
