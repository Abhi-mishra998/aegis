"""Gateway routes that bridge browser sessions to the Aegis Voice Guide
worker running on its sibling EC2.

The worker registers with LiveKit Cloud under ``agent_name='aegis-guide'``.
Browsers can't talk to the worker directly — there's no inbound port. The
handshake is: browser hits ``/voice/token`` here, gets a short-lived
LiveKit JWT carrying ``RoomAgentDispatch(agent_name='aegis-guide')``,
opens a WebRTC session to LiveKit Cloud with that token, and LiveKit
dispatches the worker into the room.

``/voice/status`` is a thin status endpoint the UI uses to render
"warming up" copy while the EC2 box wakes from auto-stop.
"""
from __future__ import annotations

import os
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["voice"])

AGENT_NAME = "aegis-guide"
# 5 minutes — matches the agent-side SESSION_MAX_SECONDS hard cap. Bounds the
# upper limit on free-tier quota burn per browser session. A reviewer can
# always click the button again to mint a fresh token.
TOKEN_TTL_SECONDS = 300


def _require_authenticated_user(request: Request) -> str:
    """Resolve the authenticated user identity for token-issuance attribution.

    The gateway's auth middleware (`services/gateway/_mw_auth.py`) sets
    ``request.state.actor`` to the JWT ``sub`` claim on every authenticated
    request. We also fall back to ``request.state.jwt_claims['sub']`` and
    ``user_id`` in case a future middleware reshape changes the canonical
    attribute. If neither is present the request was unauthenticated.
    """
    user_id = (
        getattr(request.state, "actor", None)
        or getattr(request.state, "user_id", None)
    )
    if not user_id:
        claims = getattr(request.state, "jwt_claims", None) or {}
        user_id = claims.get("sub") or claims.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    return str(user_id)


@router.get("/voice/token")
async def voice_token(request: Request) -> dict[str, Any]:
    """Mint a LiveKit JWT bound to a fresh room with the Aegis Voice Guide
    dispatch baked in.

    The browser uses ``token`` + ``url`` to open a WebRTC session against
    LiveKit Cloud. ``room`` is unique per call so two reviewers don't end
    up in the same room. ``identity`` carries the authenticated user_id
    so the agent's per-turn logs can attribute the conversation.
    """
    user_id = _require_authenticated_user(request)

    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    livekit_url = os.environ.get("LIVEKIT_URL")

    if not (api_key and api_secret and livekit_url):
        raise HTTPException(
            status_code=503,
            detail="voice agent not configured on this gateway",
        )

    try:
        from livekit.api import (
            AccessToken,
            RoomAgentDispatch,
            RoomConfiguration,
            VideoGrants,
        )
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="livekit-api not installed on this gateway",
        )

    room = f"aegis-voice-{uuid.uuid4().hex[:12]}"
    identity = f"user-{user_id[:8]}-{uuid.uuid4().hex[:6]}"

    token = (
        AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name(identity)
        .with_ttl(timedelta(seconds=TOKEN_TTL_SECONDS))
        .with_grants(
            VideoGrants(
                room=room,
                room_join=True,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .with_room_config(
            RoomConfiguration(
                agents=[RoomAgentDispatch(agent_name=AGENT_NAME)],
            )
        )
        .to_jwt()
    )

    return {
        "success": True,
        "data": {
            "token": token,
            "url": livekit_url,
            "room": room,
            "identity": identity,
            "agent_name": AGENT_NAME,
            "expires_in": TOKEN_TTL_SECONDS,
            # The agent enforces its own SESSION_MAX_SECONDS independently;
            # the UI uses this value to render a countdown. Agent-side default
            # is also 300s, so they match unless an operator overrides it.
            "session_max_seconds": TOKEN_TTL_SECONDS,
        },
    }


@router.get("/voice/status")
async def voice_status(request: Request) -> dict[str, Any]:
    """Report whether the Voice Guide worker appears reachable.

    This is a best-effort check — we just confirm the gateway is itself
    configured to mint tokens. We do not pre-flight the EC2 box because:
      - it's outbound-only, so we can't ping it
      - LiveKit Cloud's "is the worker registered?" API requires the
        same admin JWT that signs dispatches; making that check on every
        button-hover would chatter
    The UI shows a "warming up" state with a 10-second client-side
    timeout if no agent joins the room after a dispatch.
    """
    _require_authenticated_user(request)

    has_creds = bool(
        os.environ.get("LIVEKIT_API_KEY")
        and os.environ.get("LIVEKIT_API_SECRET")
        and os.environ.get("LIVEKIT_URL")
    )
    return {
        "success": True,
        "data": {
            "configured": has_creds,
            "agent_name": AGENT_NAME if has_creds else None,
        },
    }
