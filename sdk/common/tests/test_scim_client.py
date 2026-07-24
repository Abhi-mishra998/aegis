"""Real SCIM client tests using httpx.MockTransport. Covers the
ACTIVE / SUSPENDED / NOT_FOUND / TRANSIENT paths."""
from __future__ import annotations

import httpx
import pytest

from sdk.common.scim_client import (
    ScimClient,
    ScimClientConfig,
    ScimTransientError,
    _extract_user_id,
)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def _client(handler) -> ScimClient:
    http = httpx.AsyncClient(
        transport=_mock_transport(handler),
        headers={
            "Authorization": "Bearer test-token",
            "Accept": "application/scim+json",
        },
    )
    cfg = ScimClientConfig(
        base_url="https://scim.example/scim/v2",
        bearer_token="test-token",
    )
    return ScimClient(cfg, http_client=http)


class TestExtractUserId:
    def test_overlay_ref_stripped(self):
        assert _extract_user_id("scim://acme/Users/u_123") == "u_123"

    def test_scheme_only(self):
        assert _extract_user_id("scim://acme/x") == "x"

    def test_raw_id_passthrough(self):
        assert _extract_user_id("u_abc") == "u_abc"

    def test_empty(self):
        assert _extract_user_id("") == ""


class TestLookupUser:
    @pytest.mark.asyncio
    async def test_active_returns_active(self):
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path.endswith("/Users/u_1")
            return httpx.Response(
                200,
                json={"active": True, "id": "u_1"},
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        assert await c.lookup_user("scim://acme/Users/u_1") == "ACTIVE"

    @pytest.mark.asyncio
    async def test_active_false_returns_suspended(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"active": False, "id": "u_1"},
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        assert await c.lookup_user("scim://acme/Users/u_1") == "SUSPENDED"

    @pytest.mark.asyncio
    async def test_404_returns_not_found(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "not found"})
        c = _client(handler)
        assert await c.lookup_user("scim://acme/Users/gone") == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_5xx_raises_transient(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "down"})
        c = _client(handler)
        with pytest.raises(ScimTransientError):
            await c.lookup_user("scim://acme/Users/u_1")

    @pytest.mark.asyncio
    async def test_401_raises_transient_not_quarantine(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "bad token"})
        c = _client(handler)
        with pytest.raises(ScimTransientError):
            await c.lookup_user("scim://acme/Users/u_1")

    @pytest.mark.asyncio
    async def test_empty_ref_returns_not_found_without_http(self):
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("") == "NOT_FOUND"
        assert not called, "empty ref must not hit HTTP"


class TestSanitization:
    """SCIM id must be sanitized before URL construction — a hostile
    directory or a malformed overlay ref must not be able to inject a
    different path, add a query string, or fragment-smuggle into an
    unrelated SCIM endpoint. Refs that don't match the safe pattern
    are refused as NOT_FOUND WITHOUT hitting HTTP."""

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self):
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("scim://acme/Users/../../admin") == "NOT_FOUND"
        assert not called, "path-traversal id must not hit HTTP"

    @pytest.mark.asyncio
    async def test_query_string_injection_rejected(self):
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("u_1?admin=true") == "NOT_FOUND"
        assert not called

    @pytest.mark.asyncio
    async def test_fragment_injection_rejected(self):
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("u_1#anchor") == "NOT_FOUND"
        assert not called

    @pytest.mark.asyncio
    async def test_whitespace_rejected(self):
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("u_1 admin") == "NOT_FOUND"
        assert not called

    @pytest.mark.asyncio
    async def test_dot_rejected_prevents_upward_traversal(self):
        """`.` is rejected — even a single dot can start a traversal
        (`.env`, `..`, etc.). Real SCIM ids don't need dots."""
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        assert await c.lookup_user("u.1") == "NOT_FOUND"
        assert not called

    @pytest.mark.asyncio
    async def test_valid_uuid_id_still_works(self):
        """Sanity: legitimate UUID ids still resolve."""
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"active": True, "id": "u_1"},
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        assert await c.lookup_user(
            "scim://acme/Users/550e8400-e29b-41d4-a716-446655440000",
        ) == "ACTIVE"

    @pytest.mark.asyncio
    async def test_extra_long_id_rejected(self):
        """Cap at 256 chars — prevents an attacker-controlled directory
        from returning a giant id that could be logged unbounded."""
        called = []
        def handler(_req: httpx.Request) -> httpx.Response:
            called.append(1)
            return httpx.Response(200, json={"active": True})
        c = _client(handler)
        huge = "a" * 257
        assert await c.lookup_user(huge) == "NOT_FOUND"
        assert not called


class TestResponseBodyCap:
    """A broken or hostile directory streaming an infinite body would
    OOM the reconciler mid-batch. The stream is aborted at
    _SCIM_MAX_BYTES and the caller sees a transient error (not a
    quarantine — the request failed, we don't know the state)."""

    @pytest.mark.asyncio
    async def test_over_cap_body_raises_transient(self):
        from sdk.common.scim_client import _SCIM_MAX_BYTES
        huge = b"x" * (_SCIM_MAX_BYTES + 4096)

        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=huge,
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        with pytest.raises(ScimTransientError, match="scim_body_too_large"):
            await c.lookup_user("scim://acme/Users/u_1")

    @pytest.mark.asyncio
    async def test_malformed_json_raises_transient(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=b"not-json{{{",
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        with pytest.raises(ScimTransientError, match="scim_bad_json"):
            await c.lookup_user("scim://acme/Users/u_1")

    @pytest.mark.asyncio
    async def test_non_object_body_raises_transient(self):
        """SCIM §3.4.2 says responses are objects; a bare array is a
        directory bug. Prevents `.get(...)` on a list from AttributeError'ing."""
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json=["not", "an", "object"],
                headers={"content-type": "application/scim+json"},
            )
        c = _client(handler)
        with pytest.raises(ScimTransientError, match="scim_body_not_object"):
            await c.lookup_user("scim://acme/Users/u_1")

    def test_cap_size_reasonable(self):
        """Sanity: cap fits real SCIM /Users/{id} responses (<10KB) with
        100x headroom, and refuses attack payloads (<10 MiB)."""
        from sdk.common.scim_client import _SCIM_MAX_BYTES
        assert 100 * 1024 < _SCIM_MAX_BYTES < 10 * 1024 * 1024
