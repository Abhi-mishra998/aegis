"""Sprint EI-17 (2026-06-21) — inbound webhooks from Jira + ServiceNow.

Closes the round-trip gap noted in docs/security/servicenow-itsm-setup.md:
the EI-2 + EI-6 sprints opened tickets when Aegis opened an incident, but
resolving the ticket upstream did NOT close the Aegis incident. This
router accepts upstream resolve events + bridges them into the existing
api-svc PATCH /incidents/{id}.

Endpoints (BOTH skip-listed in services/gateway/middleware.py — no JWT):

  POST /webhooks/jira/{tenant_id}        Jira Automation rule POSTs here
  POST /webhooks/servicenow/{tenant_id}  SNOW Business Rule POSTs here

Auth: HMAC-SHA256(body) of the per-tenant webhook_secret stored on the
JiraIntegration / ServicenowIntegration row. The upstream platform signs;
we re-compute + constant-time compare. The tenant_id is in the path
(not the body) so we know WHICH secret to verify against — a single
shared endpoint would have to brute-force every tenant's secret.

Idempotent: a duplicate webhook for an already-RESOLVED Aegis incident
returns 200 with status=already_closed (no DB write).
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.config import settings
from sdk.common.auth import mesh_headers
from sdk.common.db import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["itsm-webhooks"])


# Jira: a status transition lands the issue in one of these names.
JIRA_DONE_NAMES = {"done", "closed", "resolved", "complete", "completed"}
# ServiceNow: state ids that mean "resolved or closed" per default install.
SNOW_DONE_STATES = {"6", "7", "8"}  # 6=Resolved, 7=Closed, 8=Cancelled

# Sprint EI-20 — canonical vocabulary for last_webhook_status. Locked so
# the UI + tests + `_touch_webhook_telemetry` agree. New values must be
# added here in the same PR that emits them.
#
# Intentionally OMITS the `no_config` response value — that path returns
# 200 with status=no_config BUT does not touch telemetry (the row either
# doesn't exist or lacks webhook_secret; touching it would create
# misleading "events" against a row the operator hasn't set up yet).
WEBHOOK_STATUS_VOCAB = frozenset({
    "closed",            # webhook resolved the Aegis incident
    "already_closed",    # incident was already RESOLVED — idempotent noop
    "ignored",           # not a done-like transition (e.g. comment added)
    "unknown_issue_key", # we don't know that issue/sys_id → noop
    "unknown_sys_id",    # SNOW twin of unknown_issue_key
    "no_issue_key",      # body parsed but no issue.key (malformed)
    "no_sys_id",         # SNOW twin of no_issue_key
    "bad_signature",     # HMAC verify failed
    "patch_failed",      # api-svc PATCH returned non-2xx
})


async def _touch_webhook_telemetry(
    db: AsyncSession,
    model_cls,
    tenant_id: uuid.UUID,
    status_word: str,
) -> None:
    """Best-effort UPDATE of the EI-20 telemetry columns.

    Failure here MUST NOT abort the webhook (the round-trip still
    functions; we just lose the telemetry for this one event).
    """
    from datetime import UTC, datetime
    try:
        await db.execute(
            update(model_cls)
            .where(model_cls.tenant_id == tenant_id)
            .values(
                last_webhook_received_at=datetime.now(UTC),
                last_webhook_status=status_word,
            ),
        )
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook_telemetry_touch_failed",
                       tenant_id=str(tenant_id), status=status_word,
                       error=str(exc))


def _verify_hmac(secret: str, body: bytes, signature_header: str) -> bool:
    """Constant-time HMAC-SHA256 verify. Header may be hex OR `sha256=hex`."""
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), sig.lower())


def _assert_url_tenant_matches_jwt(
    request: Request,
    url_tenant_id: uuid.UUID,
) -> None:
    """Defense-in-depth: if the request somehow arrives with a JWT-derived
    ``request.state.tenant_id`` (e.g. someone removes the skip-list in
    ``services/gateway/middleware.py`` or layers JWT auth on top of these
    webhooks in the future), refuse cross-tenant payloads.

    Today these endpoints are HMAC-only (see ``_SKIP_PATH_PREFIXES`` for
    ``/webhooks/jira/`` + ``/webhooks/servicenow/``), so the per-tenant
    ``webhook_secret`` IS the isolation mechanism — the URL ``tenant_id``
    selects which row's secret to verify against, and an attacker forging
    a webhook for tenant B would need tenant B's secret (which is a
    256-bit value minted server-side, returned exactly once to an
    OWNER/ADMIN JWT, and never re-exposed). N15 (audit 2026-06-21) flagged
    a cross-check gap that does not exist as described today; this guard
    pre-empts the regression risk.
    """
    jwt_tenant_id = getattr(request.state, "tenant_id", None)
    if jwt_tenant_id is None:
        # Skip-listed (or pre-auth phase) — HMAC is the only isolation.
        return
    if str(jwt_tenant_id) != str(url_tenant_id):
        raise HTTPException(
            status_code=403,
            detail="JWT tenant does not match webhook URL tenant",
        )


async def _patch_incident_resolved(
    request: Request,
    tenant_id: uuid.UUID,
    incident_id: uuid.UUID,
    actor: str,
) -> dict[str, Any]:
    """PATCH /incidents/{id} → status=RESOLVED via the api service.

    Uses the gateway's internal-secret + tenant header so the api service
    treats it as a trusted internal call. Returns {ok, status} for the
    caller; never raises (HTTP error → returns ok=false).
    """
    client = getattr(request.app.state, "client", None) or httpx.AsyncClient()
    url = f"{settings.API_SERVICE_URL.rstrip('/')}/incidents/{incident_id}"
    headers = {
        **mesh_headers("gateway"),
        "X-Tenant-ID":       str(tenant_id),
        "X-ACP-Actor":       actor,
        "Content-Type":      "application/json",
    }
    try:
        r = await client.patch(url, headers=headers,
                                json={"status": "RESOLVED"},
                                timeout=8.0)
        return {"ok": 200 <= r.status_code < 300, "http_status": r.status_code,
                "body": r.text[:160] if r.text else ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)}


# ── Jira inbound ──────────────────────────────────────────────────────────
@router.post("/jira/{tenant_id}", status_code=200)
async def jira_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Accept a Jira Automation webhook on issue resolution.

    Jira sends (per the Automation rule we ask the operator to set):
      { "webhookEvent": "jira:issue_updated",
        "issue": {
          "key": "SEC-42",
          "fields": { "status": { "name": "Done" } }
        }
      }
    """
    from services.identity.models import JiraIntegration  # noqa: PLC0415

    raw = await request.body()
    cfg = (await db.execute(
        select(JiraIntegration).where(
            JiraIntegration.tenant_id == tenant_id,
            JiraIntegration.enabled.is_(True),
        ),
    )).scalar_one_or_none()
    if cfg is None or not cfg.webhook_secret:
        # Don't leak "tenant exists / does not exist" — bare 200.
        logger.warning("jira_webhook_no_config", tenant_id=str(tenant_id))
        # EI-20: can't touch telemetry — no row to update OR we'd be
        # touching a row that doesn't have webhook_secret yet. Silent.
        return {"status": "no_config"}

    sig_hdr = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Atlassian-Webhook-Signature", "")
    )
    if not _verify_hmac(cfg.webhook_secret, raw, sig_hdr):
        logger.warning("jira_webhook_bad_signature",
                       tenant_id=str(tenant_id))
        # EI-20: write telemetry BEFORE raising so operator sees the
        # bad-signature event in Settings (most common "why isn't this
        # working" cause).
        await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, "bad_signature")
        raise HTTPException(status_code=401, detail="bad signature")

    # N15 (audit 2026-06-21) — defense-in-depth cross-check. No-op today
    # (request.state.tenant_id is None because /webhooks/jira/ is in the
    # middleware skip-list), but guards against a future regression that
    # adds JWT auth on top without checking the URL tenant_id.
    _assert_url_tenant_matches_jwt(request, tenant_id)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    issue = (body or {}).get("issue", {}) or {}
    issue_key = issue.get("key")
    status_name = ((issue.get("fields") or {}).get("status") or {}).get("name", "")
    if not issue_key:
        await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, "no_issue_key")
        return {"status": "no_issue_key"}
    if status_name.strip().lower() not in JIRA_DONE_NAMES:
        # Non-resolve event (e.g. issue commented, assignee changed). Ack.
        await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, "ignored")
        return {"status": "ignored", "reason": f"jira status '{status_name}' not done-like"}

    # Look up the Aegis incident by stored jira_issue_key.
    from services.api.models.incident import Incident  # noqa: PLC0415
    incident = (await db.execute(
        select(Incident).where(
            Incident.tenant_id == tenant_id,
            Incident.jira_issue_key == issue_key,
        ),
    )).scalar_one_or_none()
    if incident is None:
        logger.info("jira_webhook_unknown_issue_key",
                    issue_key=issue_key, tenant_id=str(tenant_id))
        # 200 on unknown so Jira doesn't retry forever for a hand-created
        # issue we never linked to an Aegis incident.
        await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, "unknown_issue_key")
        return {"status": "unknown_issue_key", "issue_key": issue_key}
    if (incident.status or "").upper() in ("RESOLVED", "CLOSED"):
        await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, "already_closed")
        return {"status": "already_closed", "incident_id": str(incident.id)}

    result = await _patch_incident_resolved(
        request, tenant_id, incident.id, actor=f"jira-webhook:{issue_key}",
    )
    logger.info(
        "jira_webhook_closed_incident",
        tenant_id=str(tenant_id), issue_key=issue_key,
        incident_id=str(incident.id), patch_ok=result.get("ok"),
    )
    final_status = "closed" if result.get("ok") else "patch_failed"
    await _touch_webhook_telemetry(db, JiraIntegration, tenant_id, final_status)
    return {
        "status": final_status,
        "incident_id": str(incident.id),
        "issue_key":   issue_key,
    }


# ── ServiceNow inbound ────────────────────────────────────────────────────
@router.post("/servicenow/{tenant_id}", status_code=200)
async def servicenow_webhook(
    tenant_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Accept a ServiceNow Business Rule webhook on state=Resolved/Closed.

    The Business Rule we ask the operator to set sends:
      { "sys_id": "<32-char>",
        "number": "INC0010001",
        "state":  "6" }
    """
    from services.identity.models import ServicenowIntegration  # noqa: PLC0415

    raw = await request.body()
    cfg = (await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
            ServicenowIntegration.enabled.is_(True),
        ),
    )).scalar_one_or_none()
    if cfg is None or not cfg.webhook_secret:
        logger.warning("snow_webhook_no_config", tenant_id=str(tenant_id))
        return {"status": "no_config"}

    sig_hdr = (
        request.headers.get("X-ServiceNow-Signature")
        or request.headers.get("X-Hub-Signature-256", "")
    )
    if not _verify_hmac(cfg.webhook_secret, raw, sig_hdr):
        logger.warning("snow_webhook_bad_signature",
                       tenant_id=str(tenant_id))
        await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, "bad_signature")
        raise HTTPException(status_code=401, detail="bad signature")

    # N15 (audit 2026-06-21) — defense-in-depth cross-check; see jira_webhook.
    _assert_url_tenant_matches_jwt(request, tenant_id)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sys_id = (body or {}).get("sys_id")
    state  = str((body or {}).get("state", "")).strip()
    if not sys_id:
        await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, "no_sys_id")
        return {"status": "no_sys_id"}
    if state not in SNOW_DONE_STATES:
        await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, "ignored")
        return {"status": "ignored", "reason": f"snow state '{state}' not resolved-like"}

    from services.api.models.incident import Incident  # noqa: PLC0415
    incident = (await db.execute(
        select(Incident).where(
            Incident.tenant_id == tenant_id,
            Incident.servicenow_sys_id == sys_id,
        ),
    )).scalar_one_or_none()
    if incident is None:
        logger.info("snow_webhook_unknown_sys_id",
                    sys_id=sys_id, tenant_id=str(tenant_id))
        await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, "unknown_sys_id")
        return {"status": "unknown_sys_id", "sys_id": sys_id}
    if (incident.status or "").upper() in ("RESOLVED", "CLOSED"):
        await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, "already_closed")
        return {"status": "already_closed", "incident_id": str(incident.id)}

    result = await _patch_incident_resolved(
        request, tenant_id, incident.id, actor=f"snow-webhook:{sys_id[:8]}",
    )
    logger.info(
        "snow_webhook_closed_incident",
        tenant_id=str(tenant_id), sys_id=sys_id,
        incident_id=str(incident.id), patch_ok=result.get("ok"),
    )
    final_status = "closed" if result.get("ok") else "patch_failed"
    await _touch_webhook_telemetry(db, ServicenowIntegration, tenant_id, final_status)
    return {
        "status": final_status,
        "incident_id": str(incident.id),
        "sys_id":       sys_id,
    }
