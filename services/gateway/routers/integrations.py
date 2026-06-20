"""Sprint EI-2 (2026-06-20) — per-tenant ITSM integration CRUD.

First vendor: Atlassian Jira Cloud. CRUD operations operate on a single
``jira_integrations`` row keyed by tenant_id; the executor in
``services/autonomy/webhook_executor.py:fire_jira`` reads the row at
incident-open time.

Endpoints (all role-gated via ``services/gateway/_rbac_map.py``):

  GET    /integrations/jira          — current config, api_token redacted
  PUT    /integrations/jira          — upsert (creates row if missing)
  DELETE /integrations/jira          — drop the row
  POST   /integrations/jira/test     — fire one test issue + return result

The PUT body never round-trips the api_token back out — on GET we surface
``has_api_token: bool`` instead. The Test endpoint creates a real issue
in the configured project with summary "Aegis connection test — safe to
close" so the operator sees it in Jira and can verify their wiring.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.db import get_db
from services.autonomy.webhook_executor import fire_jira

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])


# ── Pydantic IO models ──────────────────────────────────────────────────
class JiraUpsert(BaseModel):
    base_url: str = Field(min_length=8, max_length=255,
                          description="e.g. https://acme.atlassian.net")
    project_key: str = Field(min_length=1, max_length=32,
                             description="Jira project key, e.g. SEC")
    account_email: str = Field(min_length=3, max_length=255)
    api_token: str = Field(min_length=8, max_length=512,
                           description="Atlassian API token; never returned")
    default_issue_type: str = Field(default="Bug", max_length=32)
    default_priority: str | None = Field(default=None, max_length=32)
    enabled: bool = True
    auto_create_on_incident: bool = True


# ── Helpers ─────────────────────────────────────────────────────────────
def _tenant_id_from_request(request: Request) -> uuid.UUID:
    raw = getattr(request.state, "tenant_id", None)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uuid.UUID(str(raw)) if not isinstance(raw, uuid.UUID) else raw


def _to_public_dict(row) -> dict[str, Any]:
    """Public response shape — never expose api_token."""
    return {
        "id":                       str(row.id),
        "base_url":                 row.base_url,
        "project_key":              row.project_key,
        "account_email":            row.account_email,
        "has_api_token":            bool(row.api_token),
        "default_issue_type":       row.default_issue_type,
        "default_priority":         row.default_priority,
        "enabled":                  row.enabled,
        "auto_create_on_incident":  row.auto_create_on_incident,
        "created_at":               row.created_at.isoformat() if row.created_at else None,
        "updated_at":               row.updated_at.isoformat() if row.updated_at else None,
    }


# ── Routes ──────────────────────────────────────────────────────────────
@router.get("/jira")
async def get_jira_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Return the tenant's Jira config (api_token redacted), or null."""
    from services.identity.models import JiraIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(JiraIntegration).where(JiraIntegration.tenant_id == tenant_id),
    )
    row = res.scalar_one_or_none()
    return {"data": _to_public_dict(row) if row else None}


@router.put("/jira")
async def upsert_jira_config(
    request: Request,
    payload: JiraUpsert,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create or update the tenant's Jira config in one call."""
    from services.identity.models import JiraIntegration
    tenant_id = _tenant_id_from_request(request)

    # Reject obvious garbage early — base_url must look like an HTTPS URL.
    if not payload.base_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="base_url must include scheme")

    res = await db.execute(
        select(JiraIntegration).where(JiraIntegration.tenant_id == tenant_id),
    )
    row = res.scalar_one_or_none()
    if row is None:
        row = JiraIntegration(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            org_id=tenant_id,
            base_url=payload.base_url,
            project_key=payload.project_key,
            account_email=payload.account_email,
            api_token=payload.api_token,
            default_issue_type=payload.default_issue_type,
            default_priority=payload.default_priority,
            enabled=payload.enabled,
            auto_create_on_incident=payload.auto_create_on_incident,
        )
        db.add(row)
    else:
        row.base_url = payload.base_url
        row.project_key = payload.project_key
        row.account_email = payload.account_email
        row.api_token = payload.api_token
        row.default_issue_type = payload.default_issue_type
        row.default_priority = payload.default_priority
        row.enabled = payload.enabled
        row.auto_create_on_incident = payload.auto_create_on_incident

    await db.commit()
    await db.refresh(row)
    logger.info(
        "jira_config_upserted",
        tenant_id=str(tenant_id),
        project=payload.project_key,
        enabled=payload.enabled,
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": _to_public_dict(row)}


@router.delete("/jira", status_code=204)
async def delete_jira_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Remove the tenant's Jira config (incidents stop auto-creating tickets)."""
    from services.identity.models import JiraIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(JiraIntegration).where(JiraIntegration.tenant_id == tenant_id),
    )
    row = res.scalar_one_or_none()
    if row is None:
        return
    await db.delete(row)
    await db.commit()
    logger.info(
        "jira_config_deleted",
        tenant_id=str(tenant_id),
        actor=getattr(request.state, "actor", "unknown"),
    )


@router.post("/jira/test")
async def test_jira_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Create one test issue with the stored config. Returns the executor result."""
    from services.identity.models import JiraIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(JiraIntegration).where(JiraIntegration.tenant_id == tenant_id),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Jira config not set for this tenant")

    result = await fire_jira(
        summary="Aegis connection test — safe to close",
        base_url=row.base_url,
        account_email=row.account_email,
        api_token=row.api_token,
        project_key=row.project_key,
        issue_type=row.default_issue_type or "Bug",
        description=(
            "This issue was created by the Aegis Settings → Integrations → Jira "
            "Test Connection button. If you can see it, the wiring is correct. "
            "You can close this issue immediately — no action required."
        ),
        priority=row.default_priority,
        labels=["aegis", "connection-test"],
    )
    logger.info(
        "jira_test_fired",
        tenant_id=str(tenant_id),
        outcome=result.get("status"),
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": result}
