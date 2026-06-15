"""
Sprint 4 — Workspace inventory aggregator.

Returns a single payload the Dashboard.jsx hero card can render without
N+1 round trips: agent counts grouped by provider (the tag Sprint 2's
wizard writes to ``agents.metadata.provider``), by risk_level, by
status, plus the high-risk and total-agent rollups.

Lives outside the existing /agents router because the path is
``/workspace/inventory`` per PRODUCT_PLAN.md §8. The internal-secret
dependency is enforced at the gateway boundary (verify_internal_secret
on this dedicated router); the gateway adds the secret automatically
when forwarding customer-Bearer-authenticated requests.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.auth import verify_internal_secret
from sdk.common.db import get_db, get_tenant_id
from sdk.common.response import APIResponse
from services.registry.models import Agent

logger = structlog.get_logger(__name__)

router = APIRouter(
    prefix="/workspace",
    tags=["workspace"],
    dependencies=[Depends(verify_internal_secret)],
)


# Known providers — when Sprint 2's wizard wasn't used, agents have no
# `metadata.provider` tag. We bucket those as "unknown" so the Dashboard
# pie chart accounts for every row.
_KNOWN_PROVIDERS: tuple[str, ...] = (
    "anthropic", "openai", "bedrock", "langchain",
    "cursor", "claude-code", "openhands", "custom",
)
_KNOWN_RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")


@router.get(
    "/inventory",
    response_model=APIResponse[dict],
    summary="Workspace-wide agent inventory rollup (Dashboard hero data)",
)
async def workspace_inventory(
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
) -> APIResponse[dict]:
    """
    Returns a single payload with every count the Dashboard hero card needs.

    Shape:
        {
          "total": int,
          "active": int,
          "quarantined": int,
          "terminated": int,
          "high_risk": int,
          "by_provider": {"anthropic": 12, "openai": 9, ..., "unknown": 0},
          "by_risk":     {"low": 15, "medium": 18, "high": 4, "critical": 0},
          "by_status":   {"active": 35, "quarantined": 1, "terminated": 1},
          "wizard_provisioned": int,   // how many were created via Sprint 2 wizard
        }
    """
    rows = (
        await db.execute(
            select(Agent.status, Agent.risk_level, Agent.metadata_data)
            .where(Agent.tenant_id == tenant_id)
            .where(Agent.deleted_at.is_(None))
        )
    ).all()

    by_provider: dict[str, int] = {p: 0 for p in _KNOWN_PROVIDERS}
    by_provider["unknown"] = 0
    by_risk: dict[str, int] = {r: 0 for r in _KNOWN_RISK_LEVELS}
    by_status: dict[str, int] = {}
    wizard_count = 0

    total = 0
    high_risk = 0
    active = 0
    quarantined = 0
    terminated = 0

    for status_val, risk_level, metadata_data in rows:
        total += 1
        status_str = str(status_val).upper()
        by_status[status_str] = by_status.get(status_str, 0) + 1
        if status_str == "ACTIVE":
            active += 1
        elif status_str == "QUARANTINED":
            quarantined += 1
        elif status_str == "TERMINATED":
            terminated += 1

        risk_str = str(risk_level or "low").lower()
        if risk_str in by_risk:
            by_risk[risk_str] += 1
        if risk_str in ("high", "critical"):
            high_risk += 1

        meta = metadata_data or {}
        if isinstance(meta, dict):
            prov = str(meta.get("provider") or "").lower().strip()
            if prov in by_provider:
                by_provider[prov] += 1
            else:
                by_provider["unknown"] += 1
            if meta.get("wizard"):
                wizard_count += 1
        else:
            by_provider["unknown"] += 1

    return APIResponse(
        data={
            "total":               total,
            "active":              active,
            "quarantined":         quarantined,
            "terminated":          terminated,
            "high_risk":           high_risk,
            "by_provider":         by_provider,
            "by_risk":             by_risk,
            "by_status":           by_status,
            "wizard_provisioned":  wizard_count,
        },
    )
