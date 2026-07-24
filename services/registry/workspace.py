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
from typing import Annotated

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
    # SQL-side aggregation: prior version loaded every agent row
    # (status + risk_level + full metadata JSONB) into python to count.
    # A 100k-agent tenant → 100k row tuples with jsonb payloads = OOM.
    # Postgres does the counting in O(index scan) with constant python
    # memory.
    base_where = [
        Agent.tenant_id == tenant_id,
        Agent.deleted_at.is_(None),
    ]

    status_rows = (await db.execute(
        select(Agent.status, func.count())
        .where(*base_where)
        .group_by(Agent.status),
    )).all()
    risk_rows = (await db.execute(
        select(func.coalesce(Agent.risk_level, "low"), func.count())
        .where(*base_where)
        .group_by(func.coalesce(Agent.risk_level, "low")),
    )).all()
    # `metadata->>'provider'` extracts the JSONB string at that key;
    # NULL when absent, empty when set to "". Group + count in SQL.
    provider_rows = (await db.execute(
        select(
            func.lower(func.coalesce(
                Agent.metadata_data["provider"].astext, "",
            )),
            func.count(),
        )
        .where(*base_where)
        .group_by(func.lower(func.coalesce(
            Agent.metadata_data["provider"].astext, "",
        ))),
    )).all()
    # `metadata->'wizard'` present + not JSON-null + not JSON-false ≈
    # python's `bool(meta.get("wizard"))` for the shapes we actually write.
    wizard_count = int((await db.execute(
        select(func.count())
        .where(*base_where)
        .where(Agent.metadata_data["wizard"].isnot(None))
        .where(Agent.metadata_data["wizard"].astext != "false")
        .where(Agent.metadata_data["wizard"].astext != ""),
    )).scalar_one() or 0)

    by_status: dict[str, int] = {}
    active = quarantined = terminated = 0
    for status_val, cnt in status_rows:
        s = str(status_val).upper()
        by_status[s] = int(cnt)
        if s == "ACTIVE":
            active = int(cnt)
        elif s == "QUARANTINED":
            quarantined = int(cnt)
        elif s == "TERMINATED":
            terminated = int(cnt)
    total = sum(by_status.values())

    by_risk: dict[str, int] = dict.fromkeys(_KNOWN_RISK_LEVELS, 0)
    high_risk = 0
    for risk_val, cnt in risk_rows:
        r = str(risk_val).lower()
        if r in by_risk:
            by_risk[r] = int(cnt)
        if r in ("high", "critical"):
            high_risk += int(cnt)

    by_provider: dict[str, int] = dict.fromkeys(_KNOWN_PROVIDERS, 0)
    by_provider["unknown"] = 0
    for prov_val, cnt in provider_rows:
        p = str(prov_val or "").strip()
        bucket = p if p in by_provider else "unknown"
        by_provider[bucket] = by_provider.get(bucket, 0) + int(cnt)

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
