"""ATF v3.2 §14.5 — deployment lifecycle endpoint.

Exposes the state machine defined in `sdk/common/atf_lifecycle.py` so
ops can drive INSTALL → BOOTSTRAP → ENFORCE → ROTATE/UPGRADE/ROLLBACK
→ DECOMMISSION → DESTROY transitions. Each transition is itself a C3
ledgered event; the audit trail is a proof of what state the deployment
was in when.

State is stored per-tenant in Redis under `acp:lifecycle:{tenant_id}`.
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from sdk.common.atf_lifecycle import (
    IllegalTransition,
    LifecycleState,
    next_states,
    transition,
)
from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from sdk.common.roles import Role
from services.gateway.auth import verify_role

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])

_INITIAL: LifecycleState = "INSTALL"


def _key(tenant_id: str) -> str:
    return f"acp:lifecycle:{tenant_id}"


async def _load(tenant_id: str) -> LifecycleState:
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        raw = await redis.get(_key(tenant_id))
        return (raw or _INITIAL)  # type: ignore[return-value]
    finally:
        await redis.aclose()


async def _store(tenant_id: str, state: LifecycleState) -> None:
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        await redis.set(_key(tenant_id), state)
    finally:
        await redis.aclose()


@router.get("")
async def get_state(request: Request) -> APIResponse[dict[str, Any]]:
    tenant_id = str(getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", ""))
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenant context required")
    current = await _load(tenant_id)
    return APIResponse(data={
        "state": current,
        "next": sorted(next_states(current)),
    })


@router.post(
    "/transition",
    dependencies=[Depends(verify_role(Role.OWNER))],
)
async def transition_state(request: Request) -> APIResponse[dict[str, Any]]:
    """OWNER-only. Lifecycle transitions are C3 ledgered events (§14.5)
    — any state change touches the audit chain and public anchoring
    posture, so the role gate matches the one the workspace shadow-mode
    exit uses."""
    tenant_id = str(getattr(request.state, "tenant_id", "") or request.headers.get("X-Tenant-ID", ""))
    if not tenant_id:
        raise HTTPException(status_code=401, detail="tenant context required")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"body is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    target = str(body.get("target", "")).upper()
    reason = str(body.get("reason", ""))
    if not target:
        raise HTTPException(status_code=400, detail="target required")

    current = await _load(tenant_id)
    try:
        new_state = transition(current, target)  # type: ignore[arg-type]
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ATF §14.5 — every lifecycle transition is a C3 ledgered event.
    # ORDER MATTERS: emit the audit event FIRST, then update the state.
    # If audit push fails, the state stays at `current` and the caller
    # sees a 500 (retryable). If we stored first, an audit failure
    # would leave state changed without a ledger record — silent state
    # drift that only surfaces on next `GET /lifecycle`.
    redis = get_redis_client(settings.REDIS_URL, decode_responses=False)
    try:
        await push_audit_event(
            redis=redis,
            tenant_id=tenant_id,
            agent_id=None,
            action=f"lifecycle_{new_state.lower()}",
            metadata={
                "action_class": "C3",
                "from_state":   current,
                "to_state":     new_state,
                "reason":       reason,
            },
        )
    finally:
        await redis.aclose()

    # Audit succeeded → NOW mutate the state. If this write fails the
    # audit event is "stronger than reality" (an event exists for a
    # transition that didn't happen) — a lesser evil than the reverse,
    # because ops can reconcile by inspecting `GET /lifecycle` vs the
    # last audit event and either replaying the state or backing out
    # the audit event with a compensating C3 rollback event.
    await _store(tenant_id, new_state)

    logger.info("lifecycle_transition",
                tenant_id=tenant_id, from_state=current, to_state=new_state)

    # ATF §14.5 DESTROY line: "destruction produces a signed certificate
    # referencing the final anchor — the customer can forever prove what
    # existed and when it was destroyed." Fetch the cert now, while the
    # ledger still exists, and hand it back to the caller. Certificate
    # generation failure does NOT roll back the transition — the customer
    # can re-issue via POST /audit/logs/destruction-certificate for as
    # long as the audit rows are still on disk.
    cert: dict[str, Any] | None = None
    cert_error: str | None = None
    if new_state == "DESTROY":
        import httpx as _httpx
        try:
            client = request.app.state.client
            r = await client.post(
                f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/destruction-certificate",
                json={},
                headers={"X-Tenant-ID": tenant_id},
                timeout=10.0,
            )
            if r.status_code == 200:
                cert = (r.json() or {}).get("data")
            else:
                cert_error = f"audit_svc_status_{r.status_code}"
        except _httpx.HTTPError as exc:
            cert_error = f"audit_svc_unreachable: {type(exc).__name__}"
            logger.warning("destroy_cert_fetch_failed",
                           tenant_id=tenant_id, error=str(exc))

    response: dict[str, Any] = {"state": new_state, "from": current}
    if cert is not None:
        response["destruction_certificate"] = cert
    if cert_error is not None:
        response["destruction_certificate_error"] = cert_error
    return APIResponse(data=response)
