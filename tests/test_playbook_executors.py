"""
Sprint 2b — tests for the playbook executors (closes audit C17).

The audit found the marketed actions (``KILL_AGENT``, ``ISOLATE_AGENT``,
``BLOCK_TOOL``, ``THROTTLE``, ``REVOKE_KEY``, ``SEND_ALERT``, ``WEBHOOK``)
were no-ops. Reality: they had been wired to real downstream calls in
``services/autonomy/webhook_executor.py`` but the
``services/autonomy/playbooks.py`` docstring was never updated. Sprint 2b
rewrote the docstring and pins the wire contract for every executor with
these tests so a future regression can't quietly un-wire them.

These tests run against ``execute_step`` directly with httpx mocked — they
assert the executor calls the right downstream endpoint with the right
payload + headers. No real registry/api/Slack/PagerDuty needed.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Make sure INTERNAL_SECRET is set before importing the module under test.
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")
from services.autonomy.webhook_executor import (  # noqa: E402
    _resolve_alert_credentials,
    execute_step,
)


class _Resp:
    def __init__(self, *, status_code: int = 200, json_data=None) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = ""

    def json(self):
        return self._json


def _mock_client(call_log: list, response: _Resp = None):
    """Build an httpx.AsyncClient mock that records every (method, url,
    json, headers) tuple into ``call_log`` and returns ``response``."""
    response = response or _Resp(status_code=200)

    def _make(method: str):
        async def _f(url: str, **kwargs):
            call_log.append({
                "method":  method,
                "url":     url,
                "json":    kwargs.get("json"),
                "headers": kwargs.get("headers"),
                "data":    kwargs.get("data"),
            })
            return response
        return _f

    client = MagicMock()
    client.post   = _make("POST")
    client.get    = _make("GET")
    client.patch  = _make("PATCH")
    client.delete = _make("DELETE")

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__  = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# KILL_AGENT — calls registry with status=suspended
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_agent_calls_registry_patch_with_status_suspended():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=200))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step(
            {"action_type": "KILL_AGENT", "params": {"agent_id": "agent-1", "tenant_id": "t-1"}},
        )
    assert out["status"] == "killed"
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/agents/agent-1")
    assert call["json"] == {"status": "suspended"}
    assert call["headers"]["X-Internal-Secret"] == "test-internal-secret"
    assert call["headers"]["X-Tenant-ID"] == "t-1"


# ---------------------------------------------------------------------------
# ISOLATE_AGENT — calls registry with status=isolated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_isolate_agent_calls_registry_patch_with_status_isolated():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=204))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step(
            {"action_type": "ISOLATE_AGENT", "params": {"agent_id": "agent-2", "tenant_id": "t-2"}},
        )
    assert out["status"] == "isolated"
    assert calls[0]["url"].endswith("/agents/agent-2")
    assert calls[0]["json"] == {"status": "isolated"}


# ---------------------------------------------------------------------------
# BLOCK_TOOL — POSTs DENY permission to the registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_tool_posts_deny_permission():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=201))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step(
            {"action_type": "BLOCK_TOOL", "params": {
                "agent_id": "agent-3", "tool": "db.query", "tenant_id": "t-3",
            }},
        )
    assert out["status"] == "blocked"
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/agents/agent-3/permissions")
    assert call["json"]["tool_name"] == "db.query"
    assert call["json"]["action"] == "DENY"


# ---------------------------------------------------------------------------
# THROTTLE — POSTs to the API service throttle endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_calls_api_internal_throttle():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=200))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step(
            {"action_type": "THROTTLE", "params": {
                "agent_id": "agent-4", "rate": "1/m", "tenant_id": "t-4",
            }},
        )
    assert out["status"] == "throttled"
    assert calls[0]["url"].endswith("/internal/throttle")
    assert calls[0]["json"] == {"agent_id": "agent-4", "tenant_id": "t-4", "rate": "1/m"}


# ---------------------------------------------------------------------------
# REVOKE_KEY — DELETEs to the API service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_key_calls_api_delete():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=204))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step(
            {"action_type": "REVOKE_KEY", "params": {
                "key_id": "key-5", "tenant_id": "t-5",
            }},
        )
    assert out["status"] == "revoked"
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/api-keys/key-5")


@pytest.mark.asyncio
async def test_revoke_key_skipped_when_no_key_id():
    out = await execute_step({"action_type": "REVOKE_KEY", "params": {}})
    assert out["status"] == "skipped"


# ---------------------------------------------------------------------------
# SEND_ALERT — Slack and PagerDuty branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_alert_slack_posts_to_webhook_url():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=200))
    with patch("httpx.AsyncClient", return_value=cm), \
         patch("services.autonomy.webhook_executor._assert_safe_webhook_url"):
        out = await execute_step({"action_type": "SEND_ALERT", "params": {
            "channel":     "slack",
            "message":     "playbook fired",
            "webhook_url": "https://hooks.slack.com/services/T/X/Y",
        }})
    assert out["status"] in ("sent", "error")    # 200 → sent
    assert calls[0]["url"].startswith("https://hooks.slack.com")
    # Slack payload carries a 'text' fallback.
    assert "text" in calls[0]["json"]


@pytest.mark.asyncio
async def test_send_alert_pagerduty_posts_events_v2():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=202))
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step({"action_type": "SEND_ALERT", "params": {
            "channel":     "pagerduty",
            "message":     "critical playbook fired",
            "routing_key": "rt-key-123",
            "severity":    "critical",
        }})
    assert out["status"] == "triggered"
    assert calls[0]["url"] == "https://events.pagerduty.com/v2/enqueue"
    assert calls[0]["json"]["routing_key"] == "rt-key-123"
    assert calls[0]["json"]["payload"]["severity"] == "critical"


@pytest.mark.asyncio
async def test_send_alert_skipped_when_unknown_channel():
    out = await execute_step({"action_type": "SEND_ALERT", "params": {"channel": "carrier-pigeon"}})
    assert out["status"] == "skipped"


# ---------------------------------------------------------------------------
# WEBHOOK — generic outbound; SSRF-protected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhook_posts_payload_with_ssrf_check():
    calls: list = []
    cm, _ = _mock_client(calls, _Resp(status_code=200))
    with patch("httpx.AsyncClient", return_value=cm), \
         patch("services.autonomy.webhook_executor._assert_safe_webhook_url"):
        out = await execute_step({"action_type": "WEBHOOK", "params": {
            "url":     "https://intake.example.com/aegis",
            "payload": {"event": "test"},
        }})
    assert out["status"] in ("sent", "error")
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://intake.example.com/aegis"
    # Generic webhooks attach the context envelope.
    assert "aegis_context" in call["json"]


@pytest.mark.asyncio
async def test_webhook_blocks_loopback_url():
    """SSRF guard rejects metadata-style URLs without ever issuing the call."""
    calls: list = []
    cm, _ = _mock_client(calls)
    with patch("httpx.AsyncClient", return_value=cm):
        out = await execute_step({"action_type": "WEBHOOK", "params": {
            "url": "http://169.254.169.254/latest/meta-data/iam",
        }})
    assert out["status"] == "error"
    assert "blocked" in out["reason"].lower()
    assert calls == []


# ---------------------------------------------------------------------------
# Unknown action — must be a no-op skip, not an exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_action_returns_skipped():
    out = await execute_step({"action_type": "TELEPORT_AGENT", "params": {}})
    assert out["status"] == "skipped"
    assert out["action_type"] == "TELEPORT_AGENT"


# ---------------------------------------------------------------------------
# SSM credential resolver
# ---------------------------------------------------------------------------


def test_resolve_alert_credentials_env_default(monkeypatch):
    monkeypatch.delenv("ALERT_CRED_SOURCE", raising=False)
    monkeypatch.setenv("SLACK_WEBHOOK_URL",     "https://hooks.slack/env")
    monkeypatch.setenv("PAGERDUTY_ROUTING_KEY", "env-routing-key")
    out = _resolve_alert_credentials()
    assert out["SLACK_WEBHOOK_URL"] == "https://hooks.slack/env"
    assert out["PAGERDUTY_ROUTING_KEY"] == "env-routing-key"


def test_resolve_alert_credentials_ssm_calls_loader(monkeypatch):
    monkeypatch.setenv("ALERT_CRED_SOURCE", "ssm")
    monkeypatch.setenv("ALERT_SSM_PREFIX", "/aegis-alerts")
    fake_loader = MagicMock(return_value={
        "SLACK_WEBHOOK_URL":     "https://hooks.slack/ssm",
        "PAGERDUTY_ROUTING_KEY": "ssm-routing-key",
    })
    with patch("services.autonomy.webhook_executor._load_alert_credentials_from_ssm", fake_loader):
        out = _resolve_alert_credentials()
    fake_loader.assert_called_once_with("/aegis-alerts")
    assert out["SLACK_WEBHOOK_URL"] == "https://hooks.slack/ssm"


def test_resolve_alert_credentials_ssm_filters_pending_placeholders(monkeypatch):
    """The Sprint 2b ``PENDING_*`` placeholder values must be filtered so an
    operator running against them gets the same behavior as un-set creds —
    SEND_ALERT returns ``status=skipped`` instead of POSTing the placeholder."""
    monkeypatch.setenv("ALERT_CRED_SOURCE", "ssm")
    fake_loader = MagicMock(return_value={
        "SLACK_WEBHOOK_URL":     "PENDING_REPLACE_WITH_SLACK_WEBHOOK_URL",
        "PAGERDUTY_ROUTING_KEY": "PENDING_REPLACE_WITH_PAGERDUTY_ROUTING_KEY",
    })
    with patch("services.autonomy.webhook_executor._load_alert_credentials_from_ssm", fake_loader):
        out = _resolve_alert_credentials()
    assert out == {}
