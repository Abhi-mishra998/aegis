"""Sprint S5 (2026-06-19) — Hierarchical Teams CRUD.

Backs the new TeamSettings page. Five endpoints:

  GET    /teams                    list every team in the caller's tenant
                                   (flat, the UI builds the tree)
  POST   /teams                    create
  PATCH  /teams/{team_id}          update name / parent_team_id /
                                   manager_user_id / budget caps
  DELETE /teams/{team_id}          delete (members get team_id = NULL)
  POST   /teams/{team_id}/assign   assign a list of user_ids to a team

The tree-walking rollup (per-team spend + harmful-blocked count) lives
in /team/overview already — Sprint 17.3 grouped by `department` text;
Sprint S5 swaps that to `team_id` while preserving the JSON shape so
the existing /team UI tabs don't break.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.db import get_db

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/teams", tags=["teams"])


# ── Pydantic IO models ────────────────────────────────────────────────
class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_team_id: str | None = None
    manager_user_id: str | None = None
    daily_budget_usd_cap: int | None = None
    monthly_budget_usd_cap: int | None = None


class TeamUpdate(BaseModel):
    name: str | None = None
    parent_team_id: str | None = None
    manager_user_id: str | None = None
    daily_budget_usd_cap: int | None = None
    monthly_budget_usd_cap: int | None = None


class TeamAssign(BaseModel):
    user_ids: list[str]


# ── Helpers ───────────────────────────────────────────────────────────
def _tenant_id_from_request(request: Request) -> uuid.UUID:
    raw = getattr(request.state, "tenant_id", None)
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return uuid.UUID(str(raw)) if not isinstance(raw, uuid.UUID) else raw


def _to_dict(t) -> dict[str, Any]:
    return {
        "id":                     str(t.id),
        "name":                   t.name,
        "parent_team_id":         str(t.parent_team_id) if t.parent_team_id else None,
        "manager_user_id":        str(t.manager_user_id) if t.manager_user_id else None,
        "daily_budget_usd_cap":   t.daily_budget_usd_cap,
        "monthly_budget_usd_cap": t.monthly_budget_usd_cap,
        "created_at":             t.created_at.isoformat() if t.created_at else None,
        "updated_at":             t.updated_at.isoformat() if t.updated_at else None,
    }


# ── Routes ────────────────────────────────────────────────────────────
@router.get("")
async def list_teams(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.identity.models import Team
    tenant_id = _tenant_id_from_request(request)
    res = await db.execute(
        select(Team).where(Team.tenant_id == tenant_id).order_by(Team.name),
    )
    teams = [_to_dict(t) for t in res.scalars().all()]
    return {"data": teams}


@router.post("", status_code=201)
async def create_team(
    request: Request,
    payload: TeamCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.identity.models import Team
    tenant_id = _tenant_id_from_request(request)

    parent_id = uuid.UUID(payload.parent_team_id) if payload.parent_team_id else None
    if parent_id is not None:
        parent_check = await db.execute(
            select(Team).where(
                Team.id == parent_id, Team.tenant_id == tenant_id,
            ),
        )
        if parent_check.scalar_one_or_none() is None:
            raise HTTPException(status_code=400, detail="parent_team_id not in this tenant.")

    team = Team(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        org_id=tenant_id,
        name=payload.name,
        parent_team_id=parent_id,
        manager_user_id=uuid.UUID(payload.manager_user_id) if payload.manager_user_id else None,
        daily_budget_usd_cap=payload.daily_budget_usd_cap,
        monthly_budget_usd_cap=payload.monthly_budget_usd_cap,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    logger.info("team_created", tenant_id=str(tenant_id), team_id=str(team.id), name=team.name)
    return {"data": _to_dict(team)}


@router.patch("/{team_id}")
async def update_team(
    team_id: str,
    request: Request,
    payload: TeamUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.identity.models import Team
    tenant_id = _tenant_id_from_request(request)
    tid = uuid.UUID(team_id)

    res = await db.execute(
        select(Team).where(Team.id == tid, Team.tenant_id == tenant_id),
    )
    team = res.scalar_one_or_none()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found.")

    # Cycle protection: a team cannot be its own ancestor.
    if payload.parent_team_id is not None and payload.parent_team_id != "":
        new_parent_id = uuid.UUID(payload.parent_team_id)
        ancestor = new_parent_id
        seen = {tid}
        while ancestor is not None:
            if ancestor in seen:
                raise HTTPException(status_code=400, detail="Cycle: team cannot be its own ancestor.")
            seen.add(ancestor)
            anc_row = await db.execute(
                select(Team.parent_team_id).where(Team.id == ancestor, Team.tenant_id == tenant_id),
            )
            ancestor = anc_row.scalar_one_or_none()
        team.parent_team_id = new_parent_id
    elif payload.parent_team_id == "":
        team.parent_team_id = None

    if payload.name is not None:                    team.name = payload.name
    if payload.manager_user_id is not None:         team.manager_user_id = uuid.UUID(payload.manager_user_id) if payload.manager_user_id else None
    if payload.daily_budget_usd_cap is not None:    team.daily_budget_usd_cap = payload.daily_budget_usd_cap
    if payload.monthly_budget_usd_cap is not None:  team.monthly_budget_usd_cap = payload.monthly_budget_usd_cap

    team.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(team)
    return {"data": _to_dict(team)}


@router.delete("/{team_id}", status_code=204)
async def delete_team(
    team_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    from services.identity.models import Team, User
    tenant_id = _tenant_id_from_request(request)
    tid = uuid.UUID(team_id)

    # Un-assign members + clear parent links from children.
    await db.execute(
        update(User).where(User.team_id == tid, User.tenant_id == tenant_id)
            .values(team_id=None),
    )
    await db.execute(
        update(Team).where(Team.parent_team_id == tid, Team.tenant_id == tenant_id)
            .values(parent_team_id=None),
    )
    await db.execute(
        delete(Team).where(Team.id == tid, Team.tenant_id == tenant_id),
    )
    await db.commit()
    logger.info("team_deleted", tenant_id=str(tenant_id), team_id=team_id)


@router.post("/{team_id}/assign")
async def assign_users(
    team_id: str,
    request: Request,
    payload: TeamAssign,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    from services.identity.models import Team, User
    tenant_id = _tenant_id_from_request(request)
    tid = uuid.UUID(team_id)

    # Verify team belongs to caller's tenant
    res = await db.execute(
        select(Team).where(Team.id == tid, Team.tenant_id == tenant_id),
    )
    if res.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Team not found.")

    user_uuids = [uuid.UUID(u) for u in payload.user_ids]
    await db.execute(
        update(User)
            .where(User.id.in_(user_uuids), User.tenant_id == tenant_id)
            .values(team_id=tid),
    )
    await db.commit()
    return {"data": {"team_id": team_id, "assigned": len(user_uuids)}}
