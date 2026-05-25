"""
SIEM Integration Tests
======================
Unit tests for services/audit/siem.py.

All external HTTP calls are mocked via httpx.MockTransport / unittest.mock.patch.
No real network connections are made.

Run:
    .venv/bin/pytest tests/test_siem.py -v
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.audit.siem import (
    DatadogForwarder,
    SIEMEvent,
    SIEMForwarder,
    SplunkHECForwarder,
    get_siem_forwarder,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TENANT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_AGENT_ID  = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")

_SAMPLE_EVENT = SIEMEvent(
    timestamp="2026-05-01T12:00:00+00:00",
    tenant_id=str(_TENANT_ID),
    agent_id=str(_AGENT_ID),
    action="execute_tool",
    tool="read_file",
    decision="allow",
    reason=None,
    risk_score=0.15,
    request_id="req-test-001",
    event_hash="a" * 64,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _httpx_response(status_code: int, body: str = "{}") -> httpx.Response:
    """Create a fake httpx.Response."""
    return httpx.Response(
        status_code=status_code,
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://example.com"),
    )


# ---------------------------------------------------------------------------
# 1. Splunk forwarder — successful 200 response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_splunk_forwarder_success():
    fwd = SplunkHECForwarder(
        hec_url="https://splunk.example.com:8088/services/collector",
        hec_token="splunk-hec-token-test",
    )

    captured_payloads: list[dict] = []
    captured_headers: list[dict] = []

    async def _mock_post(url, *, json=None, headers=None, **kwargs):
        captured_payloads.append(json or {})
        captured_headers.append(headers or {})
        return _httpx_response(200, '{"text":"Success","code":0}')

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_mock_post)
        mock_client_cls.return_value = mock_client

        result = await fwd.forward(_SAMPLE_EVENT)

    assert result is True

    # Verify payload structure
    assert len(captured_payloads) == 1
    payload = captured_payloads[0]
    assert payload["host"] == "acp"
    assert payload["source"] == "acp:audit"
    assert payload["sourcetype"] == "acp:governance"
    assert "time" in payload
    assert payload["event"]["tenant_id"] == str(_TENANT_ID)
    assert payload["event"]["action"] == "execute_tool"

    # Verify auth header
    headers = captured_headers[0]
    assert "Authorization" in headers
    assert headers["Authorization"] == "Splunk splunk-hec-token-test"


# ---------------------------------------------------------------------------
# 2. Splunk forwarder — 400 response returns False, no exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_splunk_forwarder_failure():
    fwd = SplunkHECForwarder(
        hec_url="https://splunk.example.com:8088/services/collector",
        hec_token="bad-token",
    )

    async def _mock_post(url, *, json=None, headers=None, **kwargs):
        return _httpx_response(400, '{"text":"Invalid token","code":4}')

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_mock_post)
        mock_client_cls.return_value = mock_client

        # Must not raise — just return False
        result = await fwd.forward(_SAMPLE_EVENT)

    assert result is False


# ---------------------------------------------------------------------------
# 3. Datadog forwarder — successful 202 response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_datadog_forwarder_success():
    fwd = DatadogForwarder(
        logs_url="https://http-intake.logs.datadoghq.com/api/v2/logs",
        api_key="dd-test-api-key",
    )

    captured_payloads: list[list[dict]] = []
    captured_headers: list[dict] = []

    async def _mock_post(url, *, json=None, headers=None, **kwargs):
        captured_payloads.append(json or [])
        captured_headers.append(headers or {})
        return _httpx_response(202, "")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_mock_post)
        mock_client_cls.return_value = mock_client

        result = await fwd.forward(_SAMPLE_EVENT)

    assert result is True

    # Verify payload structure (Datadog expects a JSON array)
    assert len(captured_payloads) == 1
    items = captured_payloads[0]
    assert isinstance(items, list)
    assert len(items) == 1
    item = items[0]

    assert item["ddsource"] == "acp"
    assert "hostname" in item
    assert item["hostname"] == "acp-audit"
    assert "ddtags" in item
    # Tags should include tenant ID and decision
    assert str(_TENANT_ID) in item["ddtags"]
    assert "allow" in item["ddtags"]

    # Message is valid JSON
    msg = json.loads(item["message"])
    assert msg["action"] == "execute_tool"
    assert msg["tool"] == "read_file"
    assert msg["risk_score"] == 0.15

    # Verify DD-API-KEY header
    headers = captured_headers[0]
    assert headers.get("DD-API-KEY") == "dd-test-api-key"


# ---------------------------------------------------------------------------
# 4. SIEM disabled when SIEM_TARGET is empty
# ---------------------------------------------------------------------------


def test_siem_disabled_when_no_target():
    # Reset the module-level singleton so the test is isolated
    import services.audit.siem as siem_module
    siem_module._forwarder_instance = None

    with patch("services.audit.siem.settings") as mock_settings:
        mock_settings.SIEM_TARGET = ""
        mock_settings.SPLUNK_HEC_URL = ""
        mock_settings.SPLUNK_HEC_TOKEN = ""
        mock_settings.DATADOG_LOGS_URL = "https://http-intake.logs.datadoghq.com/api/v2/logs"
        mock_settings.DATADOG_API_KEY = ""

        result = get_siem_forwarder()

    # When no target configured, returns None
    assert result is None

    # Cleanup singleton
    siem_module._forwarder_instance = None


# ---------------------------------------------------------------------------
# 5. SIEMForwarder.forward_audit_row is a no-op when backend is None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_siem_forwarder_noop_when_no_backend():
    """SIEMForwarder with empty SIEM_TARGET silently does nothing."""
    with patch("services.audit.siem.settings") as mock_settings:
        mock_settings.SIEM_TARGET = ""
        mock_settings.SPLUNK_HEC_URL = ""
        mock_settings.SPLUNK_HEC_TOKEN = ""
        mock_settings.DATADOG_LOGS_URL = "https://http-intake.logs.datadoghq.com/api/v2/logs"
        mock_settings.DATADOG_API_KEY = ""

        fwd = SIEMForwarder()

    assert fwd._backend is None

    # forward_audit_row must not raise
    fake_row = MagicMock()
    fake_row.tenant_id = _TENANT_ID
    fake_row.agent_id = _AGENT_ID
    fake_row.action = "execute_tool"
    fake_row.tool = "read_file"
    fake_row.decision = "allow"
    fake_row.reason = None
    fake_row.request_id = "req-test"
    fake_row.event_hash = "a" * 64
    fake_row.timestamp = datetime(2026, 5, 1, tzinfo=UTC)
    fake_row.metadata_json = {}

    await fwd.forward_audit_row(fake_row)  # should not raise


# ---------------------------------------------------------------------------
# 6. SIEMEvent.from_audit_log builds correct event
# ---------------------------------------------------------------------------


def test_siem_event_from_audit_log():
    from services.audit.siem import SIEMEvent

    row = MagicMock()
    row.tenant_id = _TENANT_ID
    row.agent_id = _AGENT_ID
    row.action = "execute_tool"
    row.tool = "db.query"
    row.decision = "deny"
    row.reason = "policy violation"
    row.request_id = "req-from-log"
    row.event_hash = "c" * 64
    row.timestamp = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    row.metadata_json = {"risk_score": 0.88}

    event = SIEMEvent.from_audit_log(row)

    assert event.tenant_id == str(_TENANT_ID)
    assert event.agent_id == str(_AGENT_ID)
    assert event.action == "execute_tool"
    assert event.tool == "db.query"
    assert event.decision == "deny"
    assert event.reason == "policy violation"
    assert event.request_id == "req-from-log"
    assert event.event_hash == "c" * 64
    assert event.risk_score == 0.88
    assert "2026-05-01" in event.timestamp
