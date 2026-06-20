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
from sdk.common.db import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/webhooks", tags=["itsm-webhooks"])


# Jira: a status transition lands the issue in one of these names.
JIRA_DONE_NAMES = {"done", "closed", "resolved", "complete", "completed"}
# ServiceNow: state ids that mean "resolved or closed" per default install.
SNOW_DONE_STATES = {"6", "7", "8"}  # 6=Resolved, 7=Closed, 8=Cancelled


def _verify_hmac(secret: str, body: bytes, signature_header: str) -> bool:
    """Constant-time HMAC-SHA256 verify. Header may be hex OR `sha256=hex`."""
    if not secret or not signature_header:
        return False
    sig = signature_header.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), sig.lower())


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
        "X-Internal-Secret": settings.INTERNAL_SECRET,
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
        return {"status": "no_config"}

    sig_hdr = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Atlassian-Webhook-Signature", "")
    )
    if not _verify_hmac(cfg.webhook_secret, raw, sig_hdr):
        logger.warning("jira_webhook_bad_signature",
                       tenant_id=str(tenant_id))
        raise HTTPException(status_code=401, detail="bad signature")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    issue = (body or {}).get("issue", {}) or {}
    issue_key = issue.get("key")
    status_name = ((issue.get("fields") or {}).get("status") or {}).get("name", "")
    if not issue_key:
        return {"status": "no_issue_key"}
    if status_name.strip().lower() not in JIRA_DONE_NAMES:
        # Non-resolve event (e.g. issue commented, assignee changed). Ack.
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
        return {"status": "unknown_issue_key", "issue_key": issue_key}
    if (incident.status or "").upper() in ("RESOLVED", "CLOSED"):
        return {"status": "already_closed", "incident_id": str(incident.id)}

    result = await _patch_incident_resolved(
        request, tenant_id, incident.id, actor=f"jira-webhook:{issue_key}",
    )
    logger.info(
        "jira_webhook_closed_incident",
        tenant_id=str(tenant_id), issue_key=issue_key,
        incident_id=str(incident.id), patch_ok=result.get("ok"),
    )
    return {
        "status": "closed" if result.get("ok") else "patch_failed",
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
        raise HTTPException(status_code=401, detail="bad signature")

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    sys_id = (body or {}).get("sys_id")
    state  = str((body or {}).get("state", "")).strip()
    if not sys_id:
        return {"status": "no_sys_id"}
    if state not in SNOW_DONE_STATES:
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
        return {"status": "unknown_sys_id", "sys_id": sys_id}
    if (incident.status or "").upper() in ("RESOLVED", "CLOSED"):
        return {"status": "already_closed", "incident_id": str(incident.id)}

    result = await _patch_incident_resolved(
        request, tenant_id, incident.id, actor=f"snow-webhook:{sys_id[:8]}",
    )
    logger.info(
        "snow_webhook_closed_incident",
        tenant_id=str(tenant_id), sys_id=sys_id,
        incident_id=str(incident.id), patch_ok=result.get("ok"),
    )
    return {
        "status": "closed" if result.get("ok") else "patch_failed",
        "incident_id": str(incident.id),
        "sys_id":       sys_id,
    }
