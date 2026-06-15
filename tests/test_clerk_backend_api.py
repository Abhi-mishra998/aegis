"""
Unit tests for services/identity/clerk_backend_api.py.

Mocks httpx so the tests don't need network access. Verifies:
  - Authorization header carries Bearer + CLERK_SECRET_KEY.
  - PATCH /organizations/{id}/metadata wraps the dict in {public_metadata: ...}.
  - 4xx + transport errors raise ClerkBackendAPIError with the right code.
  - get_organization / get_user happy paths return the parsed body.
  - list_user_organizations unwraps Clerk's {data: [...]} envelope.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sdk.common.config import settings
from services.identity import clerk_backend_api


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "sk_test_dummy", raising=False)


def _build_response(status_code: int, body):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = str(body) if not isinstance(body, str) else body
    resp.json = MagicMock(return_value=body if not isinstance(body, str) else {})
    return resp


class _AsyncClientCtx:
    def __init__(self, response_factory):
        self._factory = response_factory
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def patch(self, url, headers=None, json=None):
        self.calls.append({"method": "PATCH", "url": url, "headers": headers, "json": json})
        return self._factory("PATCH", url)

    async def get(self, url, headers=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self._factory("GET", url)


# ---------------------------------------------------------------------------
# update_organization_public_metadata
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_metadata_sends_bearer_and_wraps_payload():
    captured = []

    def factory(method, url):
        captured.append({"method": method, "url": url})
        return _build_response(200, {"id": "org_001", "public_metadata": {"aegis_tenant_id": "t-uuid"}})

    ctx = _AsyncClientCtx(factory)
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        result = await clerk_backend_api.update_organization_public_metadata(
            "org_001", {"aegis_tenant_id": "t-uuid", "aegis_org_id": "o-uuid"},
        )

    assert result["public_metadata"]["aegis_tenant_id"] == "t-uuid"
    assert len(ctx.calls) == 1
    call = ctx.calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/organizations/org_001/metadata")
    assert call["headers"]["Authorization"] == "Bearer sk_test_dummy"
    assert call["json"] == {
        "public_metadata": {"aegis_tenant_id": "t-uuid", "aegis_org_id": "o-uuid"},
    }


@pytest.mark.asyncio
async def test_update_metadata_raises_on_4xx():
    def factory(method, url):
        return _build_response(404, "Organization not found")

    ctx = _AsyncClientCtx(factory)
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        with pytest.raises(clerk_backend_api.ClerkBackendAPIError) as exc:
            await clerk_backend_api.update_organization_public_metadata(
                "missing", {"x": 1},
            )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_metadata_raises_on_transport_error():
    class _TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def patch(self, *args, **kwargs):
            raise httpx.ConnectTimeout("connect timed out")

    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=_TimeoutClient()):
        with pytest.raises(clerk_backend_api.ClerkBackendAPIError) as exc:
            await clerk_backend_api.update_organization_public_metadata(
                "org_x", {"a": 1},
            )
    assert exc.value.status_code == 502


@pytest.mark.asyncio
async def test_update_metadata_rejects_empty_id():
    with pytest.raises(ValueError):
        await clerk_backend_api.update_organization_public_metadata("", {"x": 1})


@pytest.mark.asyncio
async def test_update_metadata_fails_without_secret(monkeypatch):
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", "", raising=False)
    ctx = _AsyncClientCtx(lambda m, u: _build_response(200, {}))
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        with pytest.raises(clerk_backend_api.ClerkBackendAPIError) as exc:
            await clerk_backend_api.update_organization_public_metadata(
                "org_y", {"a": 1},
            )
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# get_organization / get_user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_organization_returns_parsed_body():
    ctx = _AsyncClientCtx(lambda m, u: _build_response(200, {"id": "org_X", "name": "Acme"}))
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        result = await clerk_backend_api.get_organization("org_X")
    assert result["name"] == "Acme"


@pytest.mark.asyncio
async def test_get_user_propagates_4xx():
    ctx = _AsyncClientCtx(lambda m, u: _build_response(403, "Forbidden"))
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        with pytest.raises(clerk_backend_api.ClerkBackendAPIError) as exc:
            await clerk_backend_api.get_user("user_X")
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# list_user_organizations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_user_organizations_unwraps_data_envelope():
    body = {"data": [{"id": "mem_1", "role": "org:admin"}], "total_count": 1}
    ctx = _AsyncClientCtx(lambda m, u: _build_response(200, body))
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        result = await clerk_backend_api.list_user_organizations("user_X")
    assert result == [{"id": "mem_1", "role": "org:admin"}]


@pytest.mark.asyncio
async def test_list_user_organizations_handles_raw_list():
    body = [{"id": "mem_1"}]
    ctx = _AsyncClientCtx(lambda m, u: _build_response(200, body))
    with patch.object(clerk_backend_api.httpx, "AsyncClient", return_value=ctx):
        result = await clerk_backend_api.list_user_organizations("user_X")
    assert result == body
