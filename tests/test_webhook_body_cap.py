"""Q34 regression: outbound webhook POSTs to Jira/ServiceNow must cap
the response body so a broken/hostile downstream can't OOM the worker
(same class as Q17 OIDC, Q21 SCIM, Q30 threatintel).
"""
from __future__ import annotations

import os

# webhook_executor requires INTERNAL_SECRET at import time; set it
# before importing the module (matches the pattern in test_ei2/ei6).
os.environ.setdefault("INTERNAL_SECRET", "q34-unit-test")
os.environ.setdefault("ALERT_CRED_SOURCE", "env")

import pytest  # noqa: E402

from services.autonomy.webhook_executor import (  # noqa: E402
    _WEBHOOK_MAX_RESP_BYTES,
    _post_capped,
    _WebhookResponseTooLarge,
)


class _StreamingMock:
    """httpx-shaped mock: has .stream() returning an async context manager
    yielding an object with .status_code + .headers + async .aiter_bytes()."""
    def __init__(self, status: int, chunks: list[bytes], headers: dict | None = None):
        self._status = status
        self._chunks = chunks
        self._headers = headers or {}

    def stream(self, _method: str, _url: str, **_kw):
        return self._StreamCtx(self._status, self._chunks, self._headers)

    class _StreamCtx:
        def __init__(self, status, chunks, headers):
            self._status = status
            self._chunks = chunks
            self._headers = headers

        async def __aenter__(self):
            class _Resp:
                status_code = self._status
                headers = self._headers
                async def aiter_bytes(_self):
                    for c in self._chunks:
                        yield c
            return _Resp()

        async def __aexit__(self, *args):
            return False


@pytest.mark.asyncio
async def test_small_body_passes_through(monkeypatch):
    payload = b'{"key":"SEC-1"}'
    mock = _StreamingMock(201, [payload])

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self):
            return mock
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient", _Client)
    status, body = await _post_capped(
        "https://j.example/api", json_body={"x": 1}, headers={},
    )
    assert status == 201
    assert body == payload


@pytest.mark.asyncio
async def test_oversize_body_raises_too_large(monkeypatch):
    """Body exceeds cap → helper raises. Caller (fire_jira / fire_snow)
    catches this and returns a clean size-cap error to the operator."""
    huge = b"x" * (_WEBHOOK_MAX_RESP_BYTES + 1024)
    mock = _StreamingMock(200, [huge])

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self):
            return mock
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient", _Client)
    with pytest.raises(_WebhookResponseTooLarge):
        await _post_capped("https://j.example/api", json_body={"x": 1}, headers={})


@pytest.mark.asyncio
async def test_declared_content_length_over_cap_short_circuits(monkeypatch):
    """When the downstream is honest about Content-Length, we abort BEFORE
    consuming any bytes — a strictly faster reject than the stream loop."""
    mock = _StreamingMock(
        200, [b"whatever"],
        headers={"content-length": str(_WEBHOOK_MAX_RESP_BYTES + 1)},
    )

    class _Client:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self):
            return mock
        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient", _Client)
    with pytest.raises(_WebhookResponseTooLarge, match="declared"):
        await _post_capped("https://j.example/api", json_body={"x": 1}, headers={})


@pytest.mark.asyncio
async def test_fallback_get_path_when_no_stream_attr(monkeypatch):
    """Test doubles without .stream() fall back to .post() + post-hoc
    len-check. Small bodies pass; oversize raises."""
    class _Resp:
        status_code = 201
        text = '{"ok": true}'

    class _NoStreamClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, _url, **_kw):
            return _Resp()

    monkeypatch.setattr(
        "services.autonomy.webhook_executor.httpx.AsyncClient",
        _NoStreamClient,
    )
    status, body = await _post_capped(
        "https://j.example/api", json_body={"x": 1}, headers={},
    )
    assert status == 201
    assert body == b'{"ok": true}'


def test_cap_size_reasonable():
    """Sanity: cap fits real Jira/SNOW responses (few KB) and rejects
    attack payloads."""
    assert 100 * 1024 < _WEBHOOK_MAX_RESP_BYTES < 64 * 1024 * 1024
