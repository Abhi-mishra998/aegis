"""Tests for MCP gate outbound-auth invariants.

The gate is a trust boundary between the AGENT RUNTIME and the
DOWNSTREAM MCP SERVER. Two separate credentials:

  * MCP_GATE_BEARER_TOKEN           — inbound, agent → gate
  * MCP_GATE_DOWNSTREAM_BEARER_TOKEN — outbound, gate → downstream

The agent's inbound bearer MUST NOT leak to the downstream (that
would be a shared-secret leak across trust boundaries). The
downstream bearer is optional (some deployments run the downstream
on a trusted network).
"""
from __future__ import annotations

import importlib

import pytest
from starlette.requests import Request


def _reload(monkeypatch, **env):
    """Reload the router module with fresh env — needed for the module-level
    `_EXPECTED_BEARER` reinit."""
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    # Also default the inbound bearer so the module boots.
    monkeypatch.setenv("MCP_GATE_BEARER_TOKEN", env.get("MCP_GATE_BEARER_TOKEN", "test-in"))
    monkeypatch.setenv("ENVIRONMENT", "development")
    from services.mcp_gate import router as r
    importlib.reload(r)
    return r


def _fake_request(auth_header: str | None = None) -> Request:
    """Minimal starlette Request stub with a headers dict."""
    hdrs = [(b"content-type", b"application/json"),
            (b"accept", b"application/json"),
            (b"mcp-session-id", b"sess-abc")]
    if auth_header:
        hdrs.append((b"authorization", auth_header.encode()))
    scope = {"type": "http", "method": "POST", "path": "/mcp/messages",
             "headers": hdrs, "query_string": b""}
    return Request(scope)


class TestInboundAuthDoesNotLeak:
    def test_authorization_header_stripped_before_forward(self, monkeypatch):
        r = _reload(monkeypatch, MCP_GATE_DOWNSTREAM_BEARER_TOKEN=None)
        req = _fake_request(auth_header="Bearer inbound-agent-secret")
        fwd = r._forward_headers(req)
        # Bearer NEVER carried through to the downstream — even lowercased.
        assert "authorization" not in {k.lower() for k in fwd}
        # Legitimate MCP protocol headers ARE preserved.
        assert fwd.get("content-type") == "application/json"
        assert fwd.get("mcp-session-id") == "sess-abc"


class TestDownstreamAuthInjection:
    @pytest.mark.asyncio
    async def test_downstream_bearer_injected_when_configured(self, monkeypatch):
        """Set MCP_GATE_DOWNSTREAM_BEARER_TOKEN → outbound call carries it."""
        r = _reload(monkeypatch, MCP_GATE_DOWNSTREAM_BEARER_TOKEN="downstream-secret")

        captured: dict = {}

        _install_stub_stream_client(monkeypatch, r, captured)
        monkeypatch.setenv("MCP_GATE_DOWNSTREAM_URL", "https://downstream.example/mcp")

        await r._forward_to_downstream(b"{}", {"content-type": "application/json"})
        assert captured["headers"].get("Authorization") == "Bearer downstream-secret"

    @pytest.mark.asyncio
    async def test_downstream_bearer_absent_when_unset(self, monkeypatch):
        """No downstream bearer configured → no Authorization on outbound."""
        r = _reload(monkeypatch, MCP_GATE_DOWNSTREAM_BEARER_TOKEN=None)
        captured: dict = {}
        _install_stub_stream_client(monkeypatch, r, captured)
        monkeypatch.setenv("MCP_GATE_DOWNSTREAM_URL", "https://downstream.example/mcp")

        await r._forward_to_downstream(b"{}", {"content-type": "application/json"})
        assert "Authorization" not in captured["headers"]
        assert "authorization" not in captured["headers"]

    @pytest.mark.asyncio
    async def test_caller_headers_dict_not_mutated(self, monkeypatch):
        """Sanity: injecting downstream bearer must not mutate the caller's
        headers dict — subtle enterprise bug where a retry uses stale
        auth would be very hard to debug."""
        r = _reload(monkeypatch, MCP_GATE_DOWNSTREAM_BEARER_TOKEN="downstream-secret")
        captured: dict = {}
        _install_stub_stream_client(monkeypatch, r, captured)
        monkeypatch.setenv("MCP_GATE_DOWNSTREAM_URL", "https://downstream.example/mcp")

        caller_headers = {"content-type": "application/json"}
        await r._forward_to_downstream(b"{}", caller_headers)
        assert "Authorization" not in caller_headers, "caller dict was mutated"


def _install_stub_stream_client(monkeypatch, r, captured: dict, resp_body: bytes = b"{}"):
    """Install a stub httpx.AsyncClient that supports .stream() (the
    context-manager async iterator interface). Captures the outbound
    URL + headers into `captured`."""
    import httpx as _httpx

    class _StubStream:
        def __init__(self, url, content, headers):
            captured["url"] = url
            captured["headers"] = dict(headers or {})
            self.status_code = 200
            self.headers = _httpx.Headers({"content-type": "application/json"})
            self.request = _httpx.Request("POST", url)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return None
        async def aiter_bytes(self):
            yield resp_body

    class _StubClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        def stream(self, method, url, content=None, headers=None):
            return _StubStream(url, content, headers)

    monkeypatch.setattr(r.httpx, "AsyncClient", _StubClient)
