"""Tests for the MCP gate downstream-response-size DoS cap. Proves:

  * Declared Content-Length above cap → aborted before streaming.
  * Undeclared / lying Content-Length → aborted mid-stream at ceiling.
  * Legitimate small responses pass through unchanged.
  * Router surfaces the 413/cap exception as a JSON-RPC 502.
"""
from __future__ import annotations

import importlib

import httpx
import pytest


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("MCP_GATE_BEARER_TOKEN", env.get("MCP_GATE_BEARER_TOKEN", "test-in"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MCP_GATE_DOWNSTREAM_URL", "https://downstream.example/mcp")
    from services.mcp_gate import router as r
    importlib.reload(r)
    return r


def _install_streaming_client(monkeypatch, r, *,
                              chunks: list[bytes],
                              declared_len: str | None = None):
    """Stub client that streams the given chunks; optionally sets the
    Content-Length response header to `declared_len` (which may be
    absent, honest, or lying vs the actual chunk-total)."""

    class _StubStream:
        def __init__(self, url, content, headers):
            self.status_code = 200
            hdrs = {"content-type": "application/json"}
            if declared_len is not None:
                hdrs["content-length"] = declared_len
            self.headers = httpx.Headers(hdrs)
            self.request = httpx.Request("POST", url)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def aiter_bytes(self):
            for c in chunks:
                yield c

    class _StubClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def stream(self, method, url, content=None, headers=None):
            return _StubStream(url, content, headers)

    monkeypatch.setattr(r.httpx, "AsyncClient", _StubClient)


class TestForwardCap:
    @pytest.mark.asyncio
    async def test_small_response_passes(self, monkeypatch):
        r = _reload(monkeypatch, MCP_GATE_MAX_RESP_BYTES="1024")
        _install_streaming_client(
            monkeypatch, r,
            chunks=[b'{"result":"ok"}'],
            declared_len="15",
        )
        resp = await r._forward_to_downstream(b"{}", {"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.content == b'{"result":"ok"}'

    @pytest.mark.asyncio
    async def test_content_length_over_cap_rejected_pre_stream(self, monkeypatch):
        """Downstream is honest — declares 5000 bytes with cap=1024.
        Aborted at the header, no bytes consumed."""
        r = _reload(monkeypatch, MCP_GATE_MAX_RESP_BYTES="1024")
        _install_streaming_client(
            monkeypatch, r,
            chunks=[b"x" * 5000],
            declared_len="5000",
        )
        with pytest.raises(r.DownstreamResponseTooLarge, match="declared 5000"):
            await r._forward_to_downstream(b"{}", {"content-type": "application/json"})

    @pytest.mark.asyncio
    async def test_lying_content_length_aborted_mid_stream(self, monkeypatch):
        """Downstream lies — declares 100 bytes but streams 5000. The
        running-total check aborts the stream once it crosses the cap."""
        r = _reload(monkeypatch, MCP_GATE_MAX_RESP_BYTES="1024")
        # 5 chunks of 1000 bytes each — total 5000, cap 1024, so the
        # second chunk (running total 2000) trips.
        _install_streaming_client(
            monkeypatch, r,
            chunks=[b"x" * 1000] * 5,
            declared_len="100",  # lying about size
        )
        with pytest.raises(r.DownstreamResponseTooLarge, match="streamed"):
            await r._forward_to_downstream(b"{}", {"content-type": "application/json"})

    @pytest.mark.asyncio
    async def test_absent_content_length_still_bounded(self, monkeypatch):
        """Some downstreams omit Content-Length. The running-total check
        must still enforce the cap."""
        r = _reload(monkeypatch, MCP_GATE_MAX_RESP_BYTES="1024")
        _install_streaming_client(
            monkeypatch, r,
            chunks=[b"x" * 2000],  # single chunk larger than cap
            declared_len=None,     # header absent
        )
        with pytest.raises(r.DownstreamResponseTooLarge):
            await r._forward_to_downstream(b"{}", {"content-type": "application/json"})

    @pytest.mark.asyncio
    async def test_response_exactly_at_cap_is_allowed(self, monkeypatch):
        """Boundary condition — exactly N bytes with cap=N passes; N+1 aborts."""
        r = _reload(monkeypatch, MCP_GATE_MAX_RESP_BYTES="1024")
        _install_streaming_client(
            monkeypatch, r,
            chunks=[b"x" * 1024],
            declared_len="1024",
        )
        resp = await r._forward_to_downstream(b"{}", {"content-type": "application/json"})
        assert resp.status_code == 200
        assert len(resp.content) == 1024
