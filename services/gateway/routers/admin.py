"""Gateway admin routes — extracted from main.py in sprint-3.1.

Mounted onto the FastAPI app via `app.include_router(...)` in gateway/main.py.

Every route here proxies to an upstream service AND enforces ADMIN/SECURITY
role at the gateway. The upstream's verify_internal_secret check only proves
the request came through the gateway — it does NOT prove the JWT had the
right role. That gap was the audit-v2 §3.4 finding; closing it here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from sdk.common.config import settings
from services.gateway._helpers import (
    internal_headers,
    passthrough,
    require_admin_role,
)

router = APIRouter(tags=["admin"])


@router.get("/admin/tenants")
async def list_admin_tenants(request: Request) -> Any:
    """Proxy → Identity service: list all tenants (admin view).

    Requires ADMIN or SECURITY role. Identity additionally enforces
    internal-secret on the upstream route as defence-in-depth.
    """
    require_admin_role(request)
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/admin/tenants",
        headers=internal_headers(request),
        params=dict(request.query_params),
        timeout=10.0,
    )
    return passthrough(resp)


@router.get("/admin/tenants/{tenant_id}")
async def get_admin_tenant(tenant_id: str, request: Request) -> Any:
    """Proxy → Identity service: fetch a single tenant by id (admin view).

    Requires ADMIN or SECURITY role.
    """
    require_admin_role(request)
    resp = await request.app.state.client.get(
        f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/admin/tenants/{tenant_id}",
        headers=internal_headers(request),
        timeout=10.0,
    )
    return passthrough(resp)
