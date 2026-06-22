"""Sprint S2 (2026-06-19) — One-click Slack OAuth Connect button.

Two endpoints replace the existing paste-webhook-url + paste-HMAC-secret
flow on Settings → Webhooks:

  GET  /sso/slack/initiate    redirects to Slack's OAuth consent screen
  GET  /sso/slack/callback    exchanges the code for a bot token, picks
                              up the channel webhook, persists per
                              tenant, then bounces the browser back to
                              the Webhooks settings page.

The signed-state pattern protects against CSRF: the state is an
HMAC-SHA256 over (tenant_id, nonce, exp) keyed with JWT_SECRET_KEY,
so a third-party who replays the callback URL onto a different user's
session can't trick us into persisting a token under the wrong tenant.

The legacy `slack_webhook_url` + `slack_approval_secret` columns
(migration b9c0d1e2f3a4) are retained so existing tenants who set up
the webhook manually keep working — this OAuth flow ALSO populates
slack_webhook_url so the existing slack_approvals.py post-card path
fires identically. The new columns (bot_token / workspace_id /
channel_id) carry the OAuth richer state for future Slack-native
features.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import time
from typing import Annotated
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.config import settings
from sdk.common.db import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/sso/slack", tags=["sso-slack"])


# ── State signing ──────────────────────────────────────────────────────
_STATE_TTL_SECONDS = 600  # 10 minutes


def _sign_state(tenant_id: str, return_path: str) -> str:
    """Return an opaque URL-safe state token binding (tenant, return, nonce, exp).

    Verified at callback time by `_verify_state`. The nonce is fresh
    per redirect so the same tenant can have multiple concurrent flows
    without collision.
    """
    nonce = _secrets.token_urlsafe(16)
    exp = int(time.time()) + _STATE_TTL_SECONDS
    payload = {"t": tenant_id, "r": return_path, "n": nonce, "e": exp}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(
        settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256,
    ).hexdigest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=") + "." + sig


def _verify_state(state: str) -> dict | None:
    """Return the payload dict if the state is signed + unexpired, else None."""
    try:
        raw_b64, sig = state.split(".", 1)
        padding = "=" * (-len(raw_b64) % 4)
        raw = base64.urlsafe_b64decode(raw_b64 + padding)
        expected = hmac.new(
            settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(raw.decode())
        if payload.get("e", 0) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001 — any malformed input is a verification fail
        return None


# ── Initiate ──────────────────────────────────────────────────────────
@router.get("/initiate")
async def slack_initiate(
    request: Request,
    return_to: str = Query(default="/webhook-settings", alias="return_to"),
) -> RedirectResponse:
    """Bounce the browser to Slack's OAuth consent screen.

    Tenant id is taken from the validated JWT on the request, never
    from a query param — a logged-in user can only authorise Slack
    for the tenant they are signed in to.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID") or getattr(
        settings, "SLACK_OAUTH_CLIENT_ID", "",
    )
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail="Slack OAuth not configured (SLACK_OAUTH_CLIENT_ID missing).",
        )

    redirect_uri = _redirect_uri()
    state = _sign_state(str(tenant_id), return_to)

    # bot scopes only — Aegis does not need any user-token scopes.
    # incoming-webhook + chat:write covers the existing block-kit card
    # path + lets us post follow-ups without a per-channel install.
    scopes = "incoming-webhook,chat:write,channels:read"

    url = "https://slack.com/oauth/v2/authorize?" + urlencode({
        "client_id": client_id,
        "scope": scopes,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    logger.info("slack_oauth_initiate", tenant_id=str(tenant_id))
    return RedirectResponse(url, status_code=302)


# ── Callback ──────────────────────────────────────────────────────────
@router.get("/callback")
async def slack_callback(
    code: Annotated[str, Query()],
    state: Annotated[str, Query()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RedirectResponse:
    """Exchange the OAuth code, persist bot token + webhook, bounce home."""
    payload = _verify_state(state)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid or expired state.")

    tenant_id = payload["t"]
    return_to = payload.get("r") or "/webhook-settings"

    client_id = os.environ.get("SLACK_OAUTH_CLIENT_ID") or getattr(
        settings, "SLACK_OAUTH_CLIENT_ID", "",
    )
    client_secret = os.environ.get("SLACK_OAUTH_CLIENT_SECRET") or getattr(
        settings, "SLACK_OAUTH_CLIENT_SECRET", "",
    )
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="Slack OAuth not configured.")

    redirect_uri = _redirect_uri()

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "code":          code,
                "redirect_uri":  redirect_uri,
            },
        )
    body = resp.json()
    if not body.get("ok"):
        logger.warning(
            "slack_oauth_callback_failed",
            tenant_id=tenant_id, error=body.get("error", "unknown"),
        )
        return RedirectResponse(
            f"{return_to}?error=slack_oauth_failed&detail={body.get('error', 'unknown')}",
            status_code=302,
        )

    bot_token   = body.get("access_token", "")
    team        = body.get("team") or {}
    workspace_id= team.get("id", "")
    incoming    = body.get("incoming_webhook") or {}
    webhook_url = incoming.get("url", "")
    channel_id  = incoming.get("channel_id", "")

    # Persist. Reuse the existing slack_approvals.sign_link secret if
    # one already exists; otherwise mint a fresh per-tenant secret so
    # the existing slack_approvals.py post-card path keeps working.
    from services.identity.models import Tenant
    from sqlalchemy import select
    res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = res.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    tenant.slack_bot_token    = bot_token
    tenant.slack_workspace_id = workspace_id
    tenant.slack_channel_id   = channel_id
    tenant.slack_webhook_url  = webhook_url
    if not tenant.slack_approval_secret:
        tenant.slack_approval_secret = _secrets.token_hex(32)
    await db.commit()

    logger.info(
        "slack_oauth_callback_ok",
        tenant_id=tenant_id, workspace_id=workspace_id, channel_id=channel_id,
    )
    return RedirectResponse(f"{return_to}?ok=slack", status_code=302)


# ── Status (UI uses this to render "Connected to {workspace}") ────────
@router.get("/status")
async def slack_status(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Slack OAuth connection status.

    The ``tenants`` table lives in the ``acp_identity`` schema; the
    gateway runs against ``acp_audit`` and does not have it locally.
    Treat UndefinedTableError as "not connected" so the UI's Slack
    connect CTA renders without throwing — same graceful-degrade
    pattern as services/gateway/routers/teams.py:list_teams (2026-06-22).
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from services.identity.models import Tenant
    from sqlalchemy import select
    from sqlalchemy.exc import ProgrammingError
    try:
        res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res.scalar_one_or_none()
    except ProgrammingError as exc:
        logger.warning(
            "slack_status_undefined_table",
            error=str(exc).split("\n", 1)[0][:200],
        )
        await db.rollback()
        return {"connected": False}
    if tenant is None:
        return {"connected": False}
    return {
        "connected":    bool(tenant.slack_bot_token),
        "workspace_id": tenant.slack_workspace_id or "",
        "channel_id":   tenant.slack_channel_id or "",
        "has_webhook":  bool(tenant.slack_webhook_url),
    }


# ── Disconnect ────────────────────────────────────────────────────────
@router.post("/disconnect")
async def slack_disconnect(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Clear the persisted bot token + webhook. Operator must re-OAuth
    to re-enable Slack approvals."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from services.identity.models import Tenant
    from sqlalchemy import select
    res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = res.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")

    tenant.slack_bot_token    = None
    tenant.slack_workspace_id = None
    tenant.slack_channel_id   = None
    tenant.slack_webhook_url  = None
    # Leave slack_approval_secret intact so reconnect doesn't invalidate
    # any in-flight signed callback URLs in the operator's inbox.
    await db.commit()
    logger.info("slack_oauth_disconnect", tenant_id=str(tenant_id))
    return {"ok": True}


# ── Helpers ───────────────────────────────────────────────────────────
def _redirect_uri() -> str:
    """Public callback URL Slack hands the user back to."""
    base = (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("ALB_PUBLIC_HOST")
        or "http://localhost:8000"
    )
    if not base.startswith("http"):
        base = "https://" + base
    return f"{base.rstrip('/')}/sso/slack/callback"
