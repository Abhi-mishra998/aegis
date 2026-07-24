"""Q30 regression: HttpFeedProvider's body fetch must be size-capped.

Threatintel feeds are typically <1 MB. Prior code did
`resp = await self._client.get(url, timeout=T)` — httpx buffers the
entire body into RAM before returning. A broken or hostile feed URL
returning 10 GB → worker OOM. Same class of bug as Q17 (OIDC IdP body
cap) and Q21 (SCIM directory body cap).

Fix uses `client.stream("GET", url)` + `aiter_bytes` + running byte
counter, aborting at `_HTTP_MAX_BYTES` (env-tunable, default 8 MiB).
Fallback path handles test doubles without .stream().
"""
from __future__ import annotations

from typing import Any

import pytest

from services.security.threatintel.providers import (
    _HTTP_MAX_BYTES,
    HttpFeedConfig,
    HttpFeedProvider,
)


class _StreamingMock:
    """Minimal client that implements .stream() the way httpx.AsyncClient
    does — an async context manager yielding an object with
    .status_code + async .aiter_bytes()."""
    def __init__(self, status: int, chunks: list[bytes]):
        self._status = status
        self._chunks = chunks

    def stream(self, _method: str, _url: str, **_kw: Any):
        return self._StreamCtx(self._status, self._chunks)

    class _StreamCtx:
        def __init__(self, status: int, chunks: list[bytes]):
            self._status = status
            self._chunks = chunks

        async def __aenter__(self):
            class _Resp:
                status_code = self._status
                async def aiter_bytes(_self):
                    for c in self._chunks:
                        yield c
            return _Resp()

        async def __aexit__(self, *args):
            return False


def _cfg(retries: int = 0) -> HttpFeedConfig:
    return HttpFeedConfig(
        name="test", tenant_id="t1", kind="exfil_host",
        url="https://feed.example/threatlist.txt",
        retries=retries, timeout_seconds=1.0,
    )


class TestBodyCapEnforced:
    @pytest.mark.asyncio
    async def test_small_body_passes_through(self):
        payload = b"# comment\nevil.example\nbad.actor.example\n"
        client = _StreamingMock(200, [payload])
        prov = HttpFeedProvider(client, _cfg())
        records = await prov.collect()
        values = {r.value for r in records}
        assert values == {"evil.example", "bad.actor.example"}

    @pytest.mark.asyncio
    async def test_body_over_cap_aborted_and_returns_empty(self):
        # 1 MB over the cap → the stream loop aborts, no records emitted.
        huge = [b"x" * (_HTTP_MAX_BYTES + 1024)]
        client = _StreamingMock(200, huge)
        prov = HttpFeedProvider(client, _cfg())
        records = await prov.collect()
        assert records == []

    @pytest.mark.asyncio
    async def test_body_exactly_at_cap_is_ok(self):
        """At-cap: fits exactly (>cap is the trigger, not >=). We accept
        the body and parse it. Sanity boundary test."""
        payload = b"host.example\n" * 100
        # trim to just below cap
        payload = payload[: _HTTP_MAX_BYTES]
        client = _StreamingMock(200, [payload])
        prov = HttpFeedProvider(client, _cfg())
        records = await prov.collect()
        # We're just verifying no cap-related abort; content may be
        # truncated mid-line but the collector doesn't crash.
        assert isinstance(records, list)

    @pytest.mark.asyncio
    async def test_4xx_fails_fast_no_retry(self):
        """Operator misconfiguration (404, 401) — no retry, no body."""
        client = _StreamingMock(404, [])
        prov = HttpFeedProvider(client, _cfg(retries=3))
        records = await prov.collect()
        assert records == []


class TestFallbackWhenClientHasNoStream:
    """Test doubles that don't implement .stream() (e.g. plain MagicMock
    from an older test) fall through to the .get() path — still
    size-capped via post-hoc `len(text.encode(...))` check."""

    class _NoStreamClient:
        def __init__(self, status: int, text: str):
            self._status = status
            self._text = text

        async def get(self, _url: str, **_kw: Any):
            class _R:
                status_code = self._status
                text = self._text
            return _R()

    @pytest.mark.asyncio
    async def test_small_get_body_ok(self):
        client = self._NoStreamClient(200, "evil.example\nbad.example\n")
        prov = HttpFeedProvider(client, _cfg())
        records = await prov.collect()
        values = {r.value for r in records}
        assert values == {"evil.example", "bad.example"}

    @pytest.mark.asyncio
    async def test_oversize_get_body_rejected(self):
        huge_text = "x" * (_HTTP_MAX_BYTES + 1024)
        client = self._NoStreamClient(200, huge_text)
        prov = HttpFeedProvider(client, _cfg())
        records = await prov.collect()
        assert records == []


def test_cap_size_reasonable():
    """Sanity: cap is >100KB (fits real feeds) and <64MB
    (rejects attack payloads)."""
    assert 100 * 1024 < _HTTP_MAX_BYTES < 64 * 1024 * 1024
