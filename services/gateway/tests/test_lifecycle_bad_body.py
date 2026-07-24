"""Tests for the lifecycle-transition body-parsing failure paths."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.gateway.routers import lifecycle


class _Req:
    """Stand-in Request that returns a controllable body from `.json()`."""

    def __init__(self, tenant_id: str, body: object | Exception):
        self.state = SimpleNamespace(tenant_id=tenant_id)
        self.headers = {}
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.mark.asyncio
async def test_non_json_body_returns_400():
    req = _Req("tenant-1", ValueError("not json"))
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition_state(req)  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert "body is not JSON" in exc.value.detail


@pytest.mark.asyncio
async def test_non_object_body_returns_400():
    req = _Req("tenant-1", ["not", "an", "object"])
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition_state(req)  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert "must be a JSON object" in exc.value.detail


@pytest.mark.asyncio
async def test_missing_target_returns_400():
    req = _Req("tenant-1", {"reason": "no target"})
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition_state(req)  # type: ignore[arg-type]
    assert exc.value.status_code == 400
    assert "target required" in exc.value.detail


@pytest.mark.asyncio
async def test_empty_target_returns_400():
    req = _Req("tenant-1", {"target": ""})
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition_state(req)  # type: ignore[arg-type]
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_missing_tenant_returns_401():
    req = _Req("", {"target": "BOOTSTRAP"})
    with pytest.raises(HTTPException) as exc:
        await lifecycle.transition_state(req)  # type: ignore[arg-type]
    assert exc.value.status_code == 401
    assert "tenant context required" in exc.value.detail
