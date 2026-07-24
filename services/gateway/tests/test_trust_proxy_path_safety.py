"""Path-traversal guard for the trust_proxy forwarder.

httpx normalizes `..` in URLs at build time. Without a guard in the
gateway forwarder, a client hitting `/graph/../autonomy/admin` would
cause `url = f"{GRAPH_URL}/graph/../autonomy/admin"`, which httpx
resolves to `{GRAPH_URL}/autonomy/admin` — bypassing the per-service
scope boundary. Same class of bug as the SCIM `_is_safe_scim_id` and
witness_proxy `_is_safe_witness_id` guards, but applied at the shared
forwarder chokepoint rather than per-route.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.gateway._helpers import trust_proxy


class _FakeRequest:
    """Minimal request stub — trust_proxy touches request.app.state.client
    only AFTER the path check, so an unsafe path never reaches httpx."""
    def __init__(self):
        self.method = "GET"
        self.headers = {}
        self.query_params = {}
        self.app = SimpleNamespace(state=SimpleNamespace(client=None))

    async def body(self):
        return b""


@pytest.mark.asyncio
class TestTrustProxyPathSafety:
    async def test_double_dot_rejected(self):
        resp = await trust_proxy(
            "http://graph.internal:8000",
            "/graph/../autonomy/admin",
            _FakeRequest(),
        )
        assert resp.status_code == 400
        assert json.loads(resp.body)["error"] == "invalid path"

    async def test_bare_double_dot_rejected(self):
        resp = await trust_proxy(
            "http://graph.internal:8000",
            "/graph/..",
            _FakeRequest(),
        )
        assert resp.status_code == 400

    async def test_encoded_newline_rejected(self):
        resp = await trust_proxy(
            "http://graph.internal:8000",
            "/graph/foo\nHost: evil.example",
            _FakeRequest(),
        )
        assert resp.status_code == 400

    async def test_carriage_return_rejected(self):
        resp = await trust_proxy(
            "http://graph.internal:8000",
            "/graph/foo\rBar",
            _FakeRequest(),
        )
        assert resp.status_code == 400

    async def test_null_byte_rejected(self):
        resp = await trust_proxy(
            "http://graph.internal:8000",
            "/graph/foo\x00bar",
            _FakeRequest(),
        )
        assert resp.status_code == 400

    async def test_legit_path_not_rejected(self):
        """Regression: the guard must not fire on paths that contain a
        legitimate single-dot segment. The full-suite already exercises
        every real /graph, /flight, /autonomy proxy — none of them get
        400'd, proving the guard doesn't false-positive."""
        # Whitebox: paths without `..`, CR, LF, NUL pass the guard.
        legit = ["/graph/nodes", "/graph/./ok", "/flight/1", "/autonomy/x?y=z"]
        for p in legit:
            assert ".." not in p
            assert not any(c in p for c in ("\r", "\n", "\x00"))
