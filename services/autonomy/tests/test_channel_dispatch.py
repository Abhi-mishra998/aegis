"""Real tests for the Teams + canonical-webhook fire_* helpers.
Exercises SSRF guard + payload shape + error handling paths."""
from __future__ import annotations

import os

os.environ.setdefault("INTERNAL_SECRET", "test-secret")  # noqa: E402

import pytest

from services.autonomy import webhook_executor as wx


@pytest.mark.asyncio
async def test_fire_teams_skipped_when_no_url():
    r = await wx.fire_teams("msg", webhook_url="")
    assert r["status"] == "skipped"


@pytest.mark.asyncio
async def test_fire_teams_blocked_by_ssrf_guard():
    # loopback host — the SSRF validator refuses.
    r = await wx.fire_teams("msg", webhook_url="http://127.0.0.1/hook")
    assert r["status"] == "error"
    assert "blocked" in r["reason"]


@pytest.mark.asyncio
async def test_fire_teams_posts_adaptive_card(monkeypatch):
    """Patch the httpx client to capture the outgoing request."""
    captured = {}

    class _StubResp:
        status_code = 200

    class _StubClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _StubResp()

    monkeypatch.setattr(wx.httpx, "AsyncClient", _StubClient)
    # Bypass SSRF guard by pointing at a public FQDN
    r = await wx.fire_teams(
        "msg",
        webhook_url="https://acme.webhook.office.com/webhookb2/xyz",
        context={"agent_id": "ag_1", "tenant_id": "acme"},
    )
    assert r["status"] == "posted"
    body = captured["json"]
    assert body["type"] == "message"
    adap = body["attachments"][0]["content"]
    assert adap["type"] == "AdaptiveCard"
    # FactSet appended from context
    factsets = [b for b in adap["body"] if b.get("type") == "FactSet"]
    assert factsets and any(f["title"] == "agent_id" for f in factsets[0]["facts"])


@pytest.mark.asyncio
async def test_fire_webhook_skipped_when_no_url():
    r = await wx.fire_webhook("", {"any": "body"})
    assert r["status"] == "skipped"


@pytest.mark.asyncio
async def test_fire_webhook_blocked_ssrf():
    r = await wx.fire_webhook("http://169.254.169.254/latest/meta-data", {"x": 1})
    assert r["status"] == "error"


@pytest.mark.asyncio
async def test_fire_webhook_posts_body(monkeypatch):
    """SSRF guard is bypassed via monkeypatch so the test doesn't depend
    on DNS. The guard itself is exercised by test_fire_webhook_blocked_ssrf."""
    captured = {}
    class _StubResp:
        status_code = 202
    class _StubClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json=None):
            captured["url"] = url
            captured["json"] = json
            return _StubResp()
    monkeypatch.setattr(wx.httpx, "AsyncClient", _StubClient)
    monkeypatch.setattr(wx, "validate_outbound_url", lambda *a, **kw: None)
    r = await wx.fire_webhook(
        "https://itsm.acme.com/hooks/aegis",
        {"aegis_escalation": {"gate_decision_id": "gd_1"}},
    )
    assert r["status"] == "posted"
    assert captured["url"].startswith("https://")
    assert captured["json"]["aegis_escalation"]["gate_decision_id"] == "gd_1"


@pytest.mark.asyncio
async def test_fire_webhook_5xx_returns_error(monkeypatch):
    class _StubResp:
        status_code = 503
    class _StubClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, url, json=None):
            return _StubResp()
    monkeypatch.setattr(wx.httpx, "AsyncClient", _StubClient)
    monkeypatch.setattr(wx, "validate_outbound_url", lambda *a, **kw: None)
    r = await wx.fire_webhook("https://itsm.acme.com/hook", {"x": 1})
    assert r["status"] == "error"
    assert r["http_status"] == 503
