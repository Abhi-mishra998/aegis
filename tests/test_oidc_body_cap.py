"""OIDC discovery + JWKS fetches enforce a hard body-size cap.

Without this, a hostile or MITM'd IdP could stream an unbounded response
and OOM the identity service. httpx.AsyncClient buffers the full body by
default when you call `resp.json()` — the guard is streaming + early
abort at _IDP_MAX_BYTES.
"""
from __future__ import annotations

import httpx
import pytest

from services.identity import oidc


def _mock_transport(payload: bytes, status: int = 200) -> httpx.MockTransport:
    async def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload,
                              headers={"content-type": "application/json"})
    return httpx.MockTransport(_handler)


@pytest.mark.asyncio
async def test_fetch_json_capped_accepts_small_body(monkeypatch):
    """Small doc round-trips fine — cap doesn't over-block."""
    import json as _json
    body = _json.dumps({"issuer": "https://example.test"}).encode()
    _orig = httpx.AsyncClient
    def _mk(*a, **kw):
        kw["transport"] = _mock_transport(body)
        return _orig(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _mk)

    doc = await oidc._fetch_json_capped("https://example.test/tiny.json")
    assert doc == {"issuer": "https://example.test"}


@pytest.mark.asyncio
async def test_fetch_json_capped_rejects_over_cap(monkeypatch):
    """Over-cap body must raise, not buffer."""
    huge = b"x" * (oidc._IDP_MAX_BYTES + 1024)
    _orig = httpx.AsyncClient
    def _mk(*a, **kw):
        kw["transport"] = _mock_transport(huge)
        return _orig(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _mk)

    with pytest.raises(ValueError, match="exceeded"):
        await oidc._fetch_json_capped("https://evil.test/huge")


@pytest.mark.asyncio
async def test_fetch_json_capped_propagates_http_error(monkeypatch):
    """5xx must propagate — the cap doesn't swallow upstream errors."""
    _orig = httpx.AsyncClient
    def _mk(*a, **kw):
        kw["transport"] = _mock_transport(b"", status=500)
        return _orig(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _mk)

    with pytest.raises(httpx.HTTPStatusError):
        await oidc._fetch_json_capped("https://example.test/500")


def test_idp_max_bytes_reasonable():
    """Sanity: cap >100KB (fits real JWKS) and <10MB (rejects attacks)."""
    assert 100 * 1024 < oidc._IDP_MAX_BYTES < 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_fetch_json_capped_post_variant_also_caps(monkeypatch):
    """The POST/form path (used by OIDC token-exchange) shares the cap.
    Regression: an earlier revision only guarded GET, leaving the token
    endpoint (much higher-severity, since it authenticates our clients)
    unbounded."""
    huge = b"x" * (oidc._IDP_MAX_BYTES + 1024)
    _orig = httpx.AsyncClient
    def _mk(*a, **kw):
        kw["transport"] = _mock_transport(huge)
        return _orig(*a, **kw)
    monkeypatch.setattr(httpx, "AsyncClient", _mk)

    with pytest.raises(ValueError, match="exceeded"):
        await oidc._fetch_json_capped(
            "https://evil.test/token",
            method="POST",
            data={"grant_type": "authorization_code", "code": "x"},
        )
