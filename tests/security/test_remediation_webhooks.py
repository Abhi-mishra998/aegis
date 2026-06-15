"""Sprint 6 — webhook delivery tests with a deterministic httpx fake."""
from __future__ import annotations

import pytest

from services.security.remediation.webhooks import post_webhook


class _FakeResp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHttpx:
    def __init__(self, responses: list[int | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def post(self, url: str, *, content: str, headers, timeout=None):
        self.calls.append((url, content))
        if not self._responses:
            raise RuntimeError("test exhausted scripted responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return _FakeResp(nxt)


@pytest.mark.asyncio
async def test_webhook_2xx_first_try_succeeds():
    h = _FakeHttpx([200])
    ok, msg = await post_webhook(h, "https://x/hook", {"a": 1})
    assert ok is True
    assert "status=200" in msg
    assert len(h.calls) == 1


@pytest.mark.asyncio
async def test_webhook_5xx_retries_then_succeeds():
    h = _FakeHttpx([502, 200])
    ok, msg = await post_webhook(h, "https://x/hook", {"a": 1})
    assert ok is True
    assert "status=200" in msg
    assert len(h.calls) == 2


@pytest.mark.asyncio
async def test_webhook_4xx_fails_immediately_no_retry():
    h = _FakeHttpx([400])
    ok, msg = await post_webhook(h, "https://x/hook", {"a": 1})
    assert ok is False
    assert "status=400" in msg
    assert len(h.calls) == 1, "4xx must not retry — would risk rate-limits on destination"


@pytest.mark.asyncio
async def test_webhook_marks_failed_after_max_retries():
    h = _FakeHttpx([500, 502, 503, 504])
    ok, msg = await post_webhook(h, "https://x/hook", {"a": 1}, retries=3)
    assert ok is False
    assert "status=504" in msg or "status=503" in msg or "status=502" in msg
    assert len(h.calls) == 4   # initial + 3 retries


@pytest.mark.asyncio
async def test_webhook_transport_error_retries():
    h = _FakeHttpx([RuntimeError("connect refused"), 200])
    ok, msg = await post_webhook(h, "https://x/hook", {"a": 1})
    assert ok is True
    assert "status=200" in msg
    assert len(h.calls) == 2


@pytest.mark.asyncio
async def test_webhook_no_url_fails_fast():
    h = _FakeHttpx([200])  # would succeed if called
    ok, msg = await post_webhook(h, "", {"a": 1})
    assert ok is False
    assert "no webhook_url" in msg
    assert len(h.calls) == 0


@pytest.mark.asyncio
async def test_webhook_payload_rejected_when_not_json_serialisable():
    h = _FakeHttpx([200])
    # bytes objects aren't JSON-serialisable.
    ok, msg = await post_webhook(h, "https://x/hook", {"a": object()})
    assert ok is False
    assert "JSON" in msg or "serialisable" in msg
    assert len(h.calls) == 0
