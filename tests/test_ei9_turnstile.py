"""Sprint EI-9 — unit tests for the Cloudflare Turnstile verifier.

Covers:
  - No site key configured → bypass (local-dev mode)
  - Site key configured + missing token → reject
  - Site key + valid token + 200 from siteverify → allow
  - Site key + invalid token + 200 from siteverify (success: false) → reject
  - Site key + siteverify HTTP error → fail-closed reject
  - Site key + siteverify network exception → fail-closed reject
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei9-unit-test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeAppState:
    def __init__(self, client=None):
        self.client = client


class _FakeApp:
    def __init__(self, client=None):
        self.state = _FakeAppState(client=client)


class _FakeRequest:
    def __init__(self, client=None):
        self.app = _FakeApp(client=client)


def _reload_with(env: dict[str, str]):
    """Apply env, reload settings + verifier so the new env is picked up."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import importlib

    import sdk.common.config as cfg
    importlib.reload(cfg)
    import services.gateway._turnstile as ts
    importlib.reload(ts)
    return ts


# ── 1. Local-dev bypass ──────────────────────────────────────────────────
def test_no_site_key_bypasses_verification():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": ""})
    allowed, reason = _run(ts.verify(_FakeRequest(), token=None, source_ip="1.1.1.1"))
    assert allowed is True
    assert reason == "dev_bypass"


def test_no_site_key_bypasses_even_with_token():
    """A stray token in dev mode does not flip behaviour — still bypass."""
    ts = _reload_with({"TURNSTILE_SECRET_KEY": ""})
    allowed, reason = _run(ts.verify(_FakeRequest(), token="garbage", source_ip="1.1.1.1"))
    assert allowed is True
    assert reason == "dev_bypass"


# ── 2. Site key set, missing token ───────────────────────────────────────
def test_site_key_set_missing_token_rejected():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})
    allowed, reason = _run(ts.verify(_FakeRequest(), token=None, source_ip="2.2.2.2"))
    assert allowed is False
    assert reason == "missing_token"


def test_site_key_set_empty_token_rejected():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})
    allowed, reason = _run(ts.verify(_FakeRequest(), token="", source_ip="2.2.2.2"))
    assert allowed is False
    assert reason == "missing_token"


# ── 3. Site key set, siteverify says success ─────────────────────────────
def test_siteverify_success_allows():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    captured: dict[str, Any] = {}

    class _Client:
        async def post(self, url, data=None, timeout=None):
            captured["url"]     = url
            captured["data"]    = data
            captured["timeout"] = timeout
            return httpx.Response(200, json={"success": True, "challenge_ts": "..."})

    req = _FakeRequest(client=_Client())
    allowed, reason = _run(ts.verify(req, token="valid-token", source_ip="3.3.3.3"))
    assert allowed is True
    assert reason == "verified"
    # Sanity: we hit the right URL and forwarded secret + remoteip
    assert "challenges.cloudflare.com" in captured["url"]
    assert captured["data"]["secret"]   == "test-secret"
    assert captured["data"]["response"] == "valid-token"
    assert captured["data"]["remoteip"] == "3.3.3.3"


# ── 4. Site key set, siteverify says reject ──────────────────────────────
def test_siteverify_rejected_surfaces_error_code():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    class _Client:
        async def post(self, url, data=None, timeout=None):
            return httpx.Response(200, json={
                "success": False,
                "error-codes": ["timeout-or-duplicate"],
            })

    req = _FakeRequest(client=_Client())
    allowed, reason = _run(ts.verify(req, token="stale-token", source_ip="4.4.4.4"))
    assert allowed is False
    assert reason == "timeout-or-duplicate"


def test_siteverify_rejected_no_error_codes_uses_default():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    class _Client:
        async def post(self, url, data=None, timeout=None):
            return httpx.Response(200, json={"success": False})

    req = _FakeRequest(client=_Client())
    allowed, reason = _run(ts.verify(req, token="x", source_ip="4.4.4.4"))
    assert allowed is False
    assert reason == "rejected"


# ── 5. Siteverify down / unreachable → fail-closed ──────────────────────
def test_siteverify_network_exception_fails_closed():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    class _ExcClient:
        async def post(self, *_a, **_k):
            raise httpx.ConnectError("upstream DNS down")

    req = _FakeRequest(client=_ExcClient())
    allowed, reason = _run(ts.verify(req, token="x", source_ip="5.5.5.5"))
    assert allowed is False
    assert reason == "verify_unreachable"


def test_siteverify_http_error_fails_closed():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    class _Client:
        async def post(self, *_a, **_k):
            return httpx.Response(503, text="Cloudflare maintenance")

    req = _FakeRequest(client=_Client())
    allowed, reason = _run(ts.verify(req, token="x", source_ip="5.5.5.5"))
    assert allowed is False
    assert reason == "http_503"


def test_siteverify_bad_json_fails_closed():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    class _Client:
        async def post(self, *_a, **_k):
            return httpx.Response(200, text="not-json")

    req = _FakeRequest(client=_Client())
    allowed, reason = _run(ts.verify(req, token="x", source_ip="6.6.6.6"))
    assert allowed is False
    assert reason == "bad_response"


# ── 6. Optional remoteip omitted when source_ip is unknown ───────────────
def test_remoteip_omitted_when_unknown():
    ts = _reload_with({"TURNSTILE_SECRET_KEY": "test-secret"})

    captured: dict[str, Any] = {}

    class _Client:
        async def post(self, url, data=None, timeout=None):
            captured["data"] = data
            return httpx.Response(200, json={"success": True})

    req = _FakeRequest(client=_Client())
    _run(ts.verify(req, token="x", source_ip="unknown"))
    # remoteip key should NOT be present — we don't send "unknown" upstream
    assert "remoteip" not in captured["data"]


# Restore env for other test files in the same pytest run.
def teardown_module(_mod):
    _reload_with({"TURNSTILE_SECRET_KEY": ""})
