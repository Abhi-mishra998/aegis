"""Gateway proxy routes for the autonomy service — narrow override for
the SSE-publishing endpoints.

The generic ``/autonomy/{full_path:path}`` catch-all lives in
services/gateway/routers/proxies.py and continues to handle every other
autonomy route. This module declares ONLY the routes that need to fan
out a Live-Feed SSE event on success, so the generic proxy does not get
in the way (FastAPI matches routes in declaration order — this router
MUST be registered BEFORE the proxies router in main.py).

Routes here:

  POST /autonomy/overrides    — append a human override event +
                                publish ``approval_resolved`` SSE so
                                LiveFeed closes the loop the operator
                                opened with the LANDING escalation.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from redis.asyncio import Redis

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import (
    publish_event,
    trust_proxy,
)

router = APIRouter()

# Match the pattern in routers/policy.py + routers/agents.py — get a
# module-level Redis client so we don't depend on app.state lifespan
# ordering. decode_responses=False because publish_event passes through
# a json.dumps() string and Redis pub/sub is byte-safe either way.
_redis: Redis = get_redis_client(settings.REDIS_URL, decode_responses=False)


def _resolver_email(request: Request) -> str | None:
    """Pull the resolver identity from the JWT-validated request.state.

    The gateway auth middleware writes the JWT ``sub`` claim to
    ``request.state.actor`` (services/gateway/_mw_auth.py). For Clerk
    sessions this is the user's email; for legacy HS256 it is whatever
    the ``sub`` claim contained. Falls back to None if auth ran in a
    degraded mode (e.g. an internal-secret service-to-service call).
    """
    actor = getattr(request.state, "actor", None)
    if not actor or actor == "unknown":
        return None
    return str(actor)


def _resolver_role(request: Request) -> str | None:
    role = getattr(request.state, "role", None)
    return str(role) if role else None


@router.post("/autonomy/overrides", tags=["autonomy"])
async def post_autonomy_override(request: Request) -> Any:
    """Append a human override event AND publish ``approval_resolved``.

    Proxies the call to the autonomy service (which persists the
    HumanOverrideEvent row) via the shared trust_proxy helper, then
    fans out a Live-Feed SSE event so any operator UI listening on
    ``acp:events:{tenant_id}`` sees the resolution land in real time.

    The SSE publish is wrapped in try/except — a Redis or JSON
    failure must NOT block the operator's 200 response. The audit row
    + override row are already durable in Postgres at that point.
    """
    # Read the request body BEFORE delegating to trust_proxy so we
    # can use it in the SSE payload below. Starlette buffers the body
    # internally so trust_proxy re-reading it via request.body() is
    # safe — the bytes are cached on the Request object.
    body: dict[str, Any] = {}
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}

    # Delegate to the shared proxy helper — keeps method, headers,
    # query string, and tenant context identical to the generic
    # /autonomy/{full_path:path} forwarder in routers/proxies.py.
    response = await trust_proxy(
        settings.AUTONOMY_SERVICE_URL, "/autonomy/overrides", request,
    )

    # Only publish on a successful resolution. trust_proxy returns the
    # upstream status code intact so a 4xx/5xx upstream surfaces here
    # and short-circuits the SSE fan-out.
    if response.status_code not in (200, 201):
        return response

    tenant_id = request.headers.get("X-Tenant-ID", "") or (
        str(getattr(request.state, "tenant_id", "") or "")
    )
    if not tenant_id:
        return response

    # ``event_type`` is the canonical field on services/autonomy/
    # schemas.py::OverrideIn. The UI emits:
    #   - "approval"  → operator approved
    #   - "override"  → operator rejected
    # (see ui/src/pages/ApprovalInbox.jsx::decide for the source of
    # truth). Map both into a single approval_resolved.decision string
    # so the Live-Feed consumer doesn't have to learn the upstream
    # vocabulary.
    raw_event_type = str(body.get("event_type") or "").lower()
    if raw_event_type == "approval":
        decision = "approved"
    elif raw_event_type == "override":
        decision = "rejected"
    else:
        # Some other override flavour (e.g. "comment", "annotation"). Not
        # an approval resolution → don't pollute the Live-Feed.
        return response

    request_id = body.get("request_id") or request.headers.get("X-Request-ID")
    target_id = body.get("target_id")
    agent_id_meta = None
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    if isinstance(metadata, dict):
        agent_id_meta = metadata.get("agent_id")

    payload = {
        "approval_id":     target_id or request_id,
        "decision":        decision,
        "resolver_email":  _resolver_email(request) or body.get("actor"),
        "approver_role":   _resolver_role(request) or body.get("actor_role"),
        "resolved_at":     time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "request_id":      request_id,
        "target_kind":     body.get("target_kind"),
        "target_id":       target_id,
        "reason":          body.get("reason"),
    }

    # CRITICAL: publish_event is POSITIONAL — (r, tenant_id, event_type,
    # data, *, agent_id=None). Calling it with kwargs like redis=...,
    # payload=... raises TypeError and the try/except below swallows it,
    # so the event would silently never fire. Last round this bug cost a
    # redeploy. Do NOT change the call shape.
    try:
        await publish_event(
            _redis, tenant_id, "approval_resolved", payload,
            agent_id=str(agent_id_meta) if agent_id_meta else None,
        )
    except Exception:
        pass

    return response
