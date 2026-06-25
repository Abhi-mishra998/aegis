"""Sprint EI-2/EI-6 (2026-06-20) — per-tenant ITSM integration CRUD.

Vendors:
  * Atlassian Jira Cloud      — Sprint EI-2
  * ServiceNow Table API      — Sprint EI-6 (this commit)

Each vendor has a single per-tenant config row. The autonomy webhook
executor (``services/autonomy/webhook_executor.py``) reads the row at
incident-open time; the ``incident_watcher`` auto-fires when the row
has ``auto_create_on_incident=true``.

Endpoints (all role-gated via ``services/gateway/_rbac_map.py``):

  GET    /integrations/jira          — current config, api_token redacted
  PUT    /integrations/jira          — upsert
  DELETE /integrations/jira          — drop
  POST   /integrations/jira/test     — fire one test issue

  GET    /integrations/servicenow         — current config, password redacted
  PUT    /integrations/servicenow         — upsert
  DELETE /integrations/servicenow         — drop
  POST   /integrations/servicenow/test    — open one test incident

The GET surface never round-trips the secret — it surfaces
``has_api_token`` / ``has_password`` instead. The Test endpoints create
a real ticket with summary "Aegis connection test — safe to close".
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
from sdk.common.config import settings
from services.autonomy.webhook_executor import fire_jira, fire_servicenow

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/integrations", tags=["integrations"])


# P1-5 fix 2026-06-21: the gateway's `settings.DATABASE_URL` points to the
# `acp_audit` pool (audit_user); the integrations tables (jira_integrations,
# servicenow_integrations) live in `acp_identity` (owned by identity_user).
# Querying via `get_db` returns `relation "..._integrations" does not exist`.
# This module owns its own engine + session factory bound to the
# acp_identity pool so the GET/PUT/PATCH/DELETE here hit the right schema.
#
# The URL is derived from the gateway's existing DATABASE_URL by swapping
# user + db name. Per-db credentials are in pgbouncer's userlist.txt; we
# rely on identity_user / identity_prod_pwd being present there (rendered
# at boot from SSM SecureString — see user_data).
import functools as _functools  # noqa: E402
import re as _re  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker as _async_sessionmaker  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine  # noqa: E402


def _identity_db_url() -> str:
    """Translate the gateway's acp_audit URL into the acp_identity URL.

    Input  : postgresql+asyncpg://audit_user:audit_prod_pwd@pgbouncer:6432/acp_audit
    Output : postgresql+asyncpg://identity_user:identity_prod_pwd@pgbouncer:6432/acp_identity
    """
    url = settings.DATABASE_URL
    # Replace user:password — both must match an entry in pgbouncer/userlist.txt.
    url = _re.sub(r"://[^:]+:[^@]+@", "://identity_user:identity_prod_pwd@", url, count=1)
    # Replace db name (last path segment).
    url = _re.sub(r"/acp_[a-z_]+(\?|$)", r"/acp_identity\1", url)
    return url


@_functools.lru_cache
def _identity_engine():
    return _create_async_engine(
        _identity_db_url(),
        echo=False,
        pool_pre_ping=True,
        pool_size=10, max_overflow=20, pool_timeout=15, pool_recycle=1800,
        connect_args={
            "server_settings": {
                "application_name": "acp-gateway-integrations",
                "statement_timeout": "10000",
            },
            "statement_cache_size": 0,
        },
    )


@_functools.lru_cache
def _identity_sessionmaker():
    return _async_sessionmaker(
        bind=_identity_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_identity_db():
    """FastAPI dependency: yields an AsyncSession bound to acp_identity DB.

    Use this for routes in this module that need jira_integrations,
    servicenow_integrations, or scim_tokens — tables owned by identity-svc.
    """
    async with _identity_sessionmaker()() as session:
        yield session


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
    """Public response shape — never expose api_token OR webhook_secret."""
    return {
        "id":                       str(row.id),
        "base_url":                 row.base_url,
        "project_key":              row.project_key,
        "account_email":            row.account_email,
        "has_api_token":            bool(row.api_token),
        # Sprint EI-18 — surface webhook-secret presence so the UI can
        # decide between "Generate secret" and "Rotate secret" CTAs.
        "has_webhook_secret":       bool(getattr(row, "webhook_secret", None)),
        # Sprint EI-20 — deliverability telemetry. UI renders "Last
        # received <ts> ago — status: <status>" so the operator can
        # tell at a glance whether the round-trip is alive.
        "last_webhook_received_at": (
            row.last_webhook_received_at.isoformat()
            if getattr(row, "last_webhook_received_at", None) else None
        ),
        "last_webhook_status":      getattr(row, "last_webhook_status", None),
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
    db: Annotated[AsyncSession, Depends(get_identity_db)],
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
    db: Annotated[AsyncSession, Depends(get_identity_db)],
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
    db: Annotated[AsyncSession, Depends(get_identity_db)],
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
    db: Annotated[AsyncSession, Depends(get_identity_db)],
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


# ─── ServiceNow (EI-6) ────────────────────────────────────────────────────
class ServicenowUpsert(BaseModel):
    instance_url: str = Field(min_length=8, max_length=255,
                              description="e.g. https://acme.service-now.com")
    username: str = Field(min_length=1, max_length=128,
                          description="Service-account username")
    password: str = Field(min_length=1, max_length=512,
                          description="Service-account password; never returned")
    default_urgency: int = Field(default=2, ge=1, le=3,
                                 description="1=High, 2=Medium, 3=Low")
    default_impact: int = Field(default=2, ge=1, le=3,
                                description="1=High, 2=Medium, 3=Low")
    default_category: str | None = Field(default=None, max_length=64)
    default_assignment_group: str | None = Field(default=None, max_length=64,
                                                 description="sys_id of the SNOW assignment group")
    enabled: bool = True
    auto_create_on_incident: bool = True


def _snow_to_public_dict(row) -> dict[str, Any]:
    return {
        "id":                       str(row.id),
        "instance_url":             row.instance_url,
        "username":                 row.username,
        "has_password":             bool(row.password),
        # Sprint EI-18 — webhook-secret presence (never the value).
        "has_webhook_secret":       bool(getattr(row, "webhook_secret", None)),
        # Sprint EI-20 — deliverability telemetry.
        "last_webhook_received_at": (
            row.last_webhook_received_at.isoformat()
            if getattr(row, "last_webhook_received_at", None) else None
        ),
        "last_webhook_status":      getattr(row, "last_webhook_status", None),
        "default_urgency":          row.default_urgency,
        "default_impact":           row.default_impact,
        "default_category":         row.default_category,
        "default_assignment_group": row.default_assignment_group,
        "enabled":                  row.enabled,
        "auto_create_on_incident":  row.auto_create_on_incident,
        "created_at":               row.created_at.isoformat() if row.created_at else None,
        "updated_at":               row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/servicenow")
async def get_snow_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> dict:
    from services.identity.models import ServicenowIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    return {"data": _snow_to_public_dict(row) if row else None}


@router.put("/servicenow")
async def upsert_snow_config(
    request: Request,
    payload: ServicenowUpsert,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> dict:
    from services.identity.models import ServicenowIntegration
    tenant_id = _tenant_id_from_request(request)

    if not payload.instance_url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="instance_url must include scheme")

    res = await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        row = ServicenowIntegration(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            org_id=tenant_id,
            instance_url=payload.instance_url,
            username=payload.username,
            password=payload.password,
            default_urgency=payload.default_urgency,
            default_impact=payload.default_impact,
            default_category=payload.default_category,
            default_assignment_group=payload.default_assignment_group,
            enabled=payload.enabled,
            auto_create_on_incident=payload.auto_create_on_incident,
        )
        db.add(row)
    else:
        row.instance_url             = payload.instance_url
        row.username                 = payload.username
        row.password                 = payload.password
        row.default_urgency          = payload.default_urgency
        row.default_impact           = payload.default_impact
        row.default_category         = payload.default_category
        row.default_assignment_group = payload.default_assignment_group
        row.enabled                  = payload.enabled
        row.auto_create_on_incident  = payload.auto_create_on_incident

    await db.commit()
    await db.refresh(row)
    logger.info(
        "snow_config_upserted",
        tenant_id=str(tenant_id),
        instance_url=payload.instance_url,
        enabled=payload.enabled,
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": _snow_to_public_dict(row)}


@router.delete("/servicenow", status_code=204)
async def delete_snow_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> None:
    from services.identity.models import ServicenowIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        return
    await db.delete(row)
    await db.commit()
    logger.info(
        "snow_config_deleted",
        tenant_id=str(tenant_id),
        actor=getattr(request.state, "actor", "unknown"),
    )


@router.post("/servicenow/test")
async def test_snow_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> dict:
    """Create one test incident with the stored config. Returns the executor result."""
    from services.identity.models import ServicenowIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404,
                            detail="ServiceNow config not set for this tenant")

    result = await fire_servicenow(
        short_description="Aegis connection test — safe to close",
        instance_url=row.instance_url,
        username=row.username,
        password=row.password,
        description=(
            "This incident was created by the Aegis Settings → Integrations → "
            "ServiceNow Test Connection button. If you can see it, the wiring "
            "is correct. You can resolve this incident immediately — no action "
            "required."
        ),
        urgency=row.default_urgency,
        impact=row.default_impact,
        category=row.default_category,
        assignment_group=row.default_assignment_group,
    )
    logger.info(
        "snow_test_fired",
        tenant_id=str(tenant_id),
        outcome=result.get("status"),
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": result}


# ─── Sprint EI-18 — webhook-secret rotate (round-trip enabler) ──────────
# Both endpoints share the same shape: OWNER + ADMIN gated, mint a fresh
# 32-byte hex secret, write to the integration row, return plaintext in
# the response exactly once. Subsequent GETs surface
# ``has_webhook_secret: bool`` only.

def _mint_webhook_secret() -> str:
    """64-hex-char (32-byte) secret. Matches the EI-17 column width."""
    import secrets as _s
    return _s.token_hex(32)


# Hosts the webhook builder is allowed to reflect back into a URL that
# is then pasted into a customer's Jira / SNOW config. Anything else is
# rejected to "aegisagent.in" so a forged X-Forwarded-Host header from
# an attacker proxy can't silently retarget the webhook to their domain.
# Extend via settings.PUBLIC_BASE_URL host or by editing this set —
# both paths are intentional, not config-driven, because the value
# becomes a *trust anchor* once the customer pastes it into a 3rd-party
# vendor config.
_WEBHOOK_HOST_ALLOWLIST: frozenset[str] = frozenset({
    "aegisagent.in",
    "ha.aegisagent.in",
    "dev.aegisagent.in",
})


def _webhook_base_url(request: Request, vendor: str, tenant_id: uuid.UUID) -> str:
    """Build the per-tenant webhook URL the operator pastes into Jira/SNOW.

    Honours X-Forwarded-Host when behind the ALB **and** the host is in
    a small allowlist of known Aegis frontends. Anything else (forged
    headers, oddball Hosts) falls back to ``aegisagent.in`` so a
    malicious upstream cannot retarget the customer's outbound webhook
    to an attacker domain.
    """
    raw_host = (
        request.headers.get("X-Forwarded-Host")
        or request.headers.get("Host")
        or "aegisagent.in"
    )
    # Strip port + take first comma-separated entry (some proxies stack hosts).
    candidate = raw_host.split(",", 1)[0].strip().split(":", 1)[0].lower()
    # Also honour settings.PUBLIC_BASE_URL host if it's set, to keep
    # ha-stack URLs working without touching this allowlist.
    pub_host = ""
    try:
        from urllib.parse import urlparse
        pub_url = getattr(settings, "PUBLIC_BASE_URL", "") or ""
        if pub_url:
            pub_host = (urlparse(pub_url).hostname or "").lower()
    except Exception:
        pub_host = ""
    if candidate in _WEBHOOK_HOST_ALLOWLIST or (pub_host and candidate == pub_host):
        host = candidate
    else:
        host = "aegisagent.in"
    scheme = request.headers.get("X-Forwarded-Proto") or "https"
    if scheme.lower() not in ("http", "https"):
        scheme = "https"
    return f"{scheme}://{host}/webhooks/{vendor}/{tenant_id}"


@router.post("/jira/webhook-secret/rotate", status_code=201)
async def rotate_jira_webhook_secret(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> dict:
    """Mint + return a fresh Jira webhook secret. Plaintext returned ONCE."""
    from services.identity.models import JiraIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(JiraIntegration).where(JiraIntegration.tenant_id == tenant_id),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Jira config not set — PUT /integrations/jira first",
        )
    plaintext = _mint_webhook_secret()
    row.webhook_secret = plaintext
    await db.commit()
    await db.refresh(row)
    logger.info(
        "jira_webhook_secret_rotated",
        tenant_id=str(tenant_id),
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": {
        "plaintext":            plaintext,
        "plaintext_warning":    (
            "Copy this secret now. Aegis does not store the plaintext anywhere "
            "you can retrieve it again — paste it into Jira's Automation rule "
            "(see docs/security/jira-itsm-setup.md) then dismiss this banner."
        ),
        "webhook_url":          _webhook_base_url(request, "jira", tenant_id),
        "has_webhook_secret":   True,
    }}


@router.post("/servicenow/webhook-secret/rotate", status_code=201)
async def rotate_snow_webhook_secret(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_identity_db)],
) -> dict:
    """Mint + return a fresh ServiceNow webhook secret. Plaintext returned ONCE."""
    from services.identity.models import ServicenowIntegration
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(ServicenowIntegration).where(
            ServicenowIntegration.tenant_id == tenant_id,
        ),
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="ServiceNow config not set — PUT /integrations/servicenow first",
        )
    plaintext = _mint_webhook_secret()
    row.webhook_secret = plaintext
    await db.commit()
    await db.refresh(row)
    logger.info(
        "snow_webhook_secret_rotated",
        tenant_id=str(tenant_id),
        actor=getattr(request.state, "actor", "unknown"),
    )
    return {"data": {
        "plaintext":            plaintext,
        "plaintext_warning":    (
            "Copy this secret now. Aegis does not store the plaintext anywhere "
            "you can retrieve it again — paste it into the ServiceNow Business "
            "Rule script (see docs/security/servicenow-itsm-setup.md) then "
            "dismiss this banner."
        ),
        "webhook_url":          _webhook_base_url(request, "servicenow", tenant_id),
        "has_webhook_secret":   True,
    }}
