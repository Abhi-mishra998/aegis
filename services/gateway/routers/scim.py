"""Sprint EI-3 (2026-06-20) — SCIM 2.0 directory-provisioning endpoints.

Implements the minimum surface Okta's "SCIM 2.0 with Authentication
header" connector hits during a Push Users + Push Groups configuration:

  GET    /scim/v2/ServiceProviderConfig    capability discovery
  GET    /scim/v2/ResourceTypes            "we support User + Group"
  GET    /scim/v2/Schemas                  attribute discovery

  GET    /scim/v2/Users?filter=...         list / filter
  POST   /scim/v2/Users                    create
  GET    /scim/v2/Users/{id}               read
  PUT    /scim/v2/Users/{id}               replace
  PATCH  /scim/v2/Users/{id}               partial update (activate/deactivate)
  DELETE /scim/v2/Users/{id}               soft-deprovision (is_active=false)

  GET    /scim/v2/Groups
  POST   /scim/v2/Groups
  GET    /scim/v2/Groups/{id}
  PUT    /scim/v2/Groups/{id}
  PATCH  /scim/v2/Groups/{id}
  DELETE /scim/v2/Groups/{id}

Mapping:
  SCIM User  ↔  identity.User
  SCIM Group ↔  identity.Team   (Aegis already calls them "teams" internally;
                                 the SCIM Group resource is just the spec name)

The middleware skip-list at services/gateway/middleware.py:_SKIP_PATH_PREFIXES
routes the request straight here; we authenticate via the
``Authorization: Bearer scim_<token>`` header through resolve_scim_bearer().

Error responses follow RFC 7644 §3.12 (schemas: urn:ietf:params:scim:api:
messages:2.0:Error). Okta logs the ``detail`` field verbatim into its
provisioning history, so make those messages actionable.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sdk.common.db import get_db
from services.gateway._scim_auth import resolve_scim_bearer

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/scim/v2", tags=["scim"])


# ── SCIM constants ──────────────────────────────────────────────────────
SCIM_USER_SCHEMA  = "urn:ietf:params:scim:schemas:core:2.0:User"
SCIM_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_RESP_SCHEMA  = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP_SCHEMA   = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR_SCHEMA      = "urn:ietf:params:scim:api:messages:2.0:Error"


def _scim_error(status: int, detail: str, scim_type: str | None = None) -> HTTPException:
    body: dict[str, Any] = {
        "schemas": [ERROR_SCHEMA],
        "status":  str(status),
        "detail":  detail,
    }
    if scim_type:
        body["scimType"] = scim_type
    return HTTPException(status_code=status, detail=body)


def _user_to_scim(u, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Render a User row in SCIM Core 2.0 shape."""
    parts = (u.full_name or "").split(" ", 1)
    given  = parts[0] if parts else ""
    family = parts[1] if len(parts) > 1 else ""
    return {
        "schemas":  [SCIM_USER_SCHEMA],
        "id":       str(u.id),
        "userName": u.email,
        "name": {
            "formatted":  u.full_name or u.email,
            "givenName":  given,
            "familyName": family,
        },
        "emails": [{"value": u.email, "primary": True, "type": "work"}],
        "active": bool(u.is_active),
        "meta": {
            "resourceType": "User",
            "created":      u.created_at.isoformat()  if u.created_at  else None,
            "lastModified": u.updated_at.isoformat()  if u.updated_at  else None,
            "location":     f"/scim/v2/Users/{u.id}",
        },
    }


def _team_to_scim(t) -> dict[str, Any]:
    return {
        "schemas":     [SCIM_GROUP_SCHEMA],
        "id":          str(t.id),
        "displayName": t.name,
        "members":     [],  # populated by the per-id GET if requested by Okta
        "meta": {
            "resourceType": "Group",
            "created":      t.created_at.isoformat() if t.created_at else None,
            "lastModified": t.updated_at.isoformat() if t.updated_at else None,
            "location":     f"/scim/v2/Groups/{t.id}",
        },
    }


def _parse_eq_filter(filter_str: str | None, attr: str) -> str | None:
    """Extract the value from ``attr eq "value"`` — only filter Okta uses.

    Returns None if no usable filter (we then return the full list). We don't
    implement the full RFC 7644 §3.4.2.2 filter grammar; Okta's connector
    only emits `userName eq "x@y.com"` and `displayName eq "Engineering"`.
    """
    if not filter_str:
        return None
    s = filter_str.strip()
    needle = f"{attr} eq "
    if needle.lower() not in s.lower():
        return None
    rhs = s.split(needle, 1)[-1] if needle in s else s.lower().split(needle.lower(), 1)[-1]
    rhs = rhs.strip().strip('"').strip("'")
    return rhs or None


# ── Discovery endpoints ─────────────────────────────────────────────────
@router.get("/ServiceProviderConfig")
async def service_provider_config(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await resolve_scim_bearer(request, db)
    return {
        "schemas":              ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri":     "https://aegisagent.in/docs/security/okta-scim-setup",
        "patch":                {"supported": True},
        "bulk":                 {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter":               {"supported": True, "maxResults": 200},
        "changePassword":       {"supported": False},
        "sort":                 {"supported": False},
        "etag":                 {"supported": False},
        "authenticationSchemes": [{
            "name":        "OAuth Bearer Token",
            "description": "Per-tenant bearer issued via /scim/v2/tokens",
            "specUri":     "https://www.rfc-editor.org/rfc/rfc6750",
            "type":        "oauthbearertoken",
            "primary":     True,
        }],
        "meta": {"resourceType": "ServiceProviderConfig"},
    }


@router.get("/ResourceTypes")
async def resource_types(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await resolve_scim_bearer(request, db)
    return {
        "schemas":      [LIST_RESP_SCHEMA],
        "totalResults": 2,
        "Resources": [
            {
                "schemas":  ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id":       "User",
                "name":     "User",
                "endpoint": "/Users",
                "schema":   SCIM_USER_SCHEMA,
            },
            {
                "schemas":  ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id":       "Group",
                "name":     "Group",
                "endpoint": "/Groups",
                "schema":   SCIM_GROUP_SCHEMA,
            },
        ],
    }


@router.get("/Schemas")
async def schemas(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    await resolve_scim_bearer(request, db)
    # Minimum advertised attributes — userName + emails + active for User,
    # displayName for Group. Okta only checks the attributes it intends to
    # send + filter on, so this list is intentionally compact.
    return {
        "schemas":      [LIST_RESP_SCHEMA],
        "totalResults": 2,
        "Resources": [
            {
                "id":          SCIM_USER_SCHEMA,
                "name":        "User",
                "description": "SCIM core User",
                "attributes": [
                    {"name": "userName", "type": "string", "required": True, "uniqueness": "server"},
                    {"name": "name",     "type": "complex"},
                    {"name": "emails",   "type": "complex", "multiValued": True},
                    {"name": "active",   "type": "boolean"},
                ],
            },
            {
                "id":          SCIM_GROUP_SCHEMA,
                "name":        "Group",
                "description": "SCIM core Group (mapped to Aegis Team)",
                "attributes": [
                    {"name": "displayName", "type": "string", "required": True},
                    {"name": "members",     "type": "complex", "multiValued": True},
                ],
            },
        ],
    }


# ── Users ───────────────────────────────────────────────────────────────
@router.get("/Users")
async def list_users(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User

    filter_str  = request.query_params.get("filter")
    start_index = max(1,   int(request.query_params.get("startIndex", 1)))
    count       = min(200, max(1, int(request.query_params.get("count", 50))))
    offset = start_index - 1

    where = [User.tenant_id == tenant_id]
    if filter_str:
        username = _parse_eq_filter(filter_str, "userName")
        if username is not None:
            where.append(User.email == username)

    total = (await db.execute(
        select(func.count(User.id)).where(*where),
    )).scalar_one()
    res = await db.execute(
        select(User).where(*where).order_by(User.email).offset(offset).limit(count),
    )
    users = [_user_to_scim(u, tenant_id) for u in res.scalars().all()]
    return {
        "schemas":      [LIST_RESP_SCHEMA],
        "totalResults": int(total),
        "startIndex":   start_index,
        "itemsPerPage": len(users),
        "Resources":    users,
    }


@router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "User not found")
    res = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == tenant_id),
    )
    user = res.scalar_one_or_none()
    if user is None:
        raise _scim_error(404, "User not found")
    return _user_to_scim(user, tenant_id)


@router.post("/Users", status_code=201)
async def create_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User, UserRole
    body = await request.json()

    user_name = (body.get("userName") or "").strip().lower()
    if not user_name:
        raise _scim_error(400, "userName is required", scim_type="invalidValue")

    # SCIM §3.3 — server returns 409 + scimType=uniqueness on duplicates.
    dup = await db.execute(
        select(User).where(User.tenant_id == tenant_id, User.email == user_name),
    )
    if dup.scalar_one_or_none() is not None:
        raise _scim_error(409, f"User '{user_name}' already exists",
                          scim_type="uniqueness")

    name = body.get("name") or {}
    formatted = (
        name.get("formatted")
        or " ".join(filter(None, [name.get("givenName"), name.get("familyName")]))
        or user_name
    )

    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        org_id=tenant_id,
        email=user_name,
        # SCIM POST does not carry a password — provisioned users sign in via
        # the existing SSO/Clerk path. Store a non-empty sentinel so the
        # NOT NULL constraint passes; the user can never bind to it.
        hashed_password="scim-provisioned",
        full_name=formatted,
        role=UserRole.VIEWER,
        is_active=bool(body.get("active", True)),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(
        "scim_user_created",
        tenant_id=str(tenant_id), user_id=str(user.id), user_name=user_name,
    )
    return _user_to_scim(user, tenant_id)


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "User not found")
    res = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == tenant_id),
    )
    user = res.scalar_one_or_none()
    if user is None:
        raise _scim_error(404, "User not found")

    body = await request.json()
    user_name = (body.get("userName") or user.email).strip().lower()
    name = body.get("name") or {}
    formatted = (
        name.get("formatted")
        or " ".join(filter(None, [name.get("givenName"), name.get("familyName")]))
        or user.full_name
    )
    user.email     = user_name
    user.full_name = formatted
    user.is_active = bool(body.get("active", user.is_active))
    await db.commit()
    await db.refresh(user)
    logger.info("scim_user_replaced", tenant_id=str(tenant_id), user_id=user_id)
    return _user_to_scim(user, tenant_id)


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "User not found")
    res = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == tenant_id),
    )
    user = res.scalar_one_or_none()
    if user is None:
        raise _scim_error(404, "User not found")

    body = await request.json()
    ops  = body.get("Operations") or body.get("operations") or []
    for op in ops:
        op_name = (op.get("op") or "").lower()
        path    = (op.get("path") or "").strip()
        value   = op.get("value")
        # Okta deactivates with op=replace path=active value=false
        if op_name == "replace" and path == "active":
            user.is_active = bool(value)
        elif op_name == "replace" and path == "userName" and isinstance(value, str):
            user.email = value.strip().lower()
        elif op_name == "replace" and isinstance(value, dict):
            # Body-less replace — value is a dict of attribute updates.
            if "active" in value:
                user.is_active = bool(value["active"])
            if "userName" in value:
                user.email = str(value["userName"]).strip().lower()

    await db.commit()
    await db.refresh(user)
    logger.info("scim_user_patched", tenant_id=str(tenant_id), user_id=user_id,
                ops=[o.get("op") for o in ops])
    return _user_to_scim(user, tenant_id)


@router.delete("/Users/{user_id}", status_code=204)
async def deprovision_user(
    user_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import User
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "User not found")
    res = await db.execute(
        select(User).where(User.id == uid, User.tenant_id == tenant_id),
    )
    user = res.scalar_one_or_none()
    if user is None:
        raise _scim_error(404, "User not found")
    # Soft-deprovision: SCIM DELETE means "remove from the directory" — for
    # Aegis that means is_active=false so audit history is preserved.
    user.is_active = False
    await db.commit()
    logger.info("scim_user_deprovisioned", tenant_id=str(tenant_id), user_id=user_id)
    return Response(status_code=204)


# ── Groups (mapped to Team) ─────────────────────────────────────────────
@router.get("/Groups")
async def list_groups(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team

    filter_str  = request.query_params.get("filter")
    start_index = max(1,   int(request.query_params.get("startIndex", 1)))
    count       = min(200, max(1, int(request.query_params.get("count", 50))))
    offset = start_index - 1

    where = [Team.tenant_id == tenant_id]
    if filter_str:
        name = _parse_eq_filter(filter_str, "displayName")
        if name is not None:
            where.append(Team.name == name)

    total = (await db.execute(
        select(func.count(Team.id)).where(*where),
    )).scalar_one()
    res = await db.execute(
        select(Team).where(*where).order_by(Team.name).offset(offset).limit(count),
    )
    groups = [_team_to_scim(t) for t in res.scalars().all()]
    return {
        "schemas":      [LIST_RESP_SCHEMA],
        "totalResults": int(total),
        "startIndex":   start_index,
        "itemsPerPage": len(groups),
        "Resources":    groups,
    }


@router.get("/Groups/{group_id}")
async def get_group(
    group_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team, User
    try:
        gid = uuid.UUID(group_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "Group not found")
    res = await db.execute(
        select(Team).where(Team.id == gid, Team.tenant_id == tenant_id),
    )
    team = res.scalar_one_or_none()
    if team is None:
        raise _scim_error(404, "Group not found")
    out = _team_to_scim(team)
    member_res = await db.execute(
        select(User.id, User.email).where(
            User.tenant_id == tenant_id, User.team_id == team.id,
        ),
    )
    out["members"] = [
        {"value": str(uid), "display": email, "$ref": f"/scim/v2/Users/{uid}"}
        for uid, email in member_res.all()
    ]
    return out


@router.post("/Groups", status_code=201)
async def create_group(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team
    body = await request.json()
    display = (body.get("displayName") or "").strip()
    if not display:
        raise _scim_error(400, "displayName is required", scim_type="invalidValue")

    dup = await db.execute(
        select(Team).where(Team.tenant_id == tenant_id, Team.name == display),
    )
    if dup.scalar_one_or_none() is not None:
        raise _scim_error(409, f"Group '{display}' already exists",
                          scim_type="uniqueness")

    team = Team(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        org_id=tenant_id,
        name=display,
    )
    db.add(team)
    await db.commit()
    await db.refresh(team)
    logger.info("scim_group_created", tenant_id=str(tenant_id), name=display)
    return _team_to_scim(team)


@router.put("/Groups/{group_id}")
async def replace_group(
    group_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team
    try:
        gid = uuid.UUID(group_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "Group not found")
    res = await db.execute(
        select(Team).where(Team.id == gid, Team.tenant_id == tenant_id),
    )
    team = res.scalar_one_or_none()
    if team is None:
        raise _scim_error(404, "Group not found")
    body = await request.json()
    new_name = (body.get("displayName") or team.name).strip()
    team.name = new_name
    await db.commit()
    await db.refresh(team)
    return _team_to_scim(team)


@router.patch("/Groups/{group_id}")
async def patch_group(
    group_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Handle Okta's membership add/remove patches.

    PatchOp shape Okta sends:
      {"op":"add",    "path":"members", "value":[{"value":"<userId>"}]}
      {"op":"remove", "path":"members[value eq \\"<userId>\\"]"}
    """
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team, User
    try:
        gid = uuid.UUID(group_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "Group not found")
    res = await db.execute(
        select(Team).where(Team.id == gid, Team.tenant_id == tenant_id),
    )
    team = res.scalar_one_or_none()
    if team is None:
        raise _scim_error(404, "Group not found")

    body = await request.json()
    for op in (body.get("Operations") or []):
        op_name = (op.get("op") or "").lower()
        path    = (op.get("path") or "").strip()
        value   = op.get("value")
        if op_name == "replace" and path == "displayName" and isinstance(value, str):
            team.name = value.strip()
            continue
        if op_name in ("add", "replace") and (path == "members" or path.startswith("members")):
            ids = _extract_member_ids(value)
            if ids:
                await db.execute(
                    User.__table__.update()
                    .where(
                        User.tenant_id == tenant_id,
                        User.id.in_(ids),
                    )
                    .values(team_id=team.id),
                )
        elif op_name == "remove" and path.startswith("members"):
            # path of form: members[value eq "<uid>"]
            uid = _filter_value_from_path(path)
            if uid:
                try:
                    target_uid = uuid.UUID(uid)
                except (ValueError, TypeError):
                    continue
                await db.execute(
                    User.__table__.update()
                    .where(
                        User.tenant_id == tenant_id,
                        User.id == target_uid,
                        User.team_id == team.id,
                    )
                    .values(team_id=None),
                )

    await db.commit()
    await db.refresh(team)
    return _team_to_scim(team)


@router.delete("/Groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    tenant_id = await resolve_scim_bearer(request, db)
    from services.identity.models import Team, User
    try:
        gid = uuid.UUID(group_id)
    except (ValueError, TypeError):
        raise _scim_error(404, "Group not found")
    res = await db.execute(
        select(Team).where(Team.id == gid, Team.tenant_id == tenant_id),
    )
    team = res.scalar_one_or_none()
    if team is None:
        raise _scim_error(404, "Group not found")
    # Detach members (FK is nullable + un-cascading by design) before delete.
    await db.execute(
        User.__table__.update()
        .where(User.tenant_id == tenant_id, User.team_id == team.id)
        .values(team_id=None),
    )
    await db.delete(team)
    await db.commit()
    logger.info("scim_group_deleted", tenant_id=str(tenant_id), group_id=group_id)
    return Response(status_code=204)


# ── Helpers ─────────────────────────────────────────────────────────────
def _extract_member_ids(value: Any) -> list[uuid.UUID]:
    """SCIM members value is either a list of {"value": <uid>} or a single dict."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[uuid.UUID] = []
    for item in items:
        if isinstance(item, dict):
            v = item.get("value")
            if v:
                try:
                    out.append(uuid.UUID(str(v)))
                except (ValueError, TypeError):
                    continue
    return out


def _filter_value_from_path(path: str) -> str | None:
    """Pull the bracketed value out of ``members[value eq "<uid>"]``."""
    if "[" not in path or "]" not in path:
        return None
    inner = path.split("[", 1)[1].rsplit("]", 1)[0]
    if " eq " not in inner:
        return None
    rhs = inner.split(" eq ", 1)[1].strip().strip('"').strip("'")
    return rhs or None
