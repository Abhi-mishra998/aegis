"""
Compliance Engine Tests
=======================
Unit tests for services/audit/compliance.py.

No real database required — uses AsyncMock/MagicMock fixtures to simulate
the DB query results that the compliance functions depend on.

Run:
    .venv/bin/pytest tests/test_compliance.py -v
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.audit.compliance import (
    export_bundle_as_json,
    generate_eu_ai_act_bundle,
    generate_tool_call_ledger,
)

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_TENANT_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
_AGENT_A   = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000001")
_AGENT_B   = uuid.UUID("cccccccc-0000-0000-0000-000000000001")

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
_PERIOD_START = _NOW - timedelta(days=7)
_PERIOD_END   = _NOW


# ---------------------------------------------------------------------------
# Helpers — build fake AuditLog row objects
# ---------------------------------------------------------------------------


def _make_row(
    *,
    action: str = "execute_tool",
    decision: str = "allow",
    tool: str = "read_file",
    agent_id: uuid.UUID = _AGENT_A,
    reason: str | None = None,
    risk_score: float = 0.2,
    ts: datetime | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = uuid.uuid4()
    row.tenant_id = _TENANT_ID
    row.agent_id = agent_id
    row.action = action
    row.tool = tool
    row.decision = decision
    row.reason = reason
    row.request_id = f"req-{uuid.uuid4().hex[:8]}"
    row.event_hash = "a" * 64
    row.prev_hash = "b" * 64
    row.timestamp = ts or _NOW
    row.metadata_json = {"risk_score": risk_score}
    return row


def _mock_db_for_ledger(rows: list[MagicMock]) -> AsyncMock:
    """
    Return a mock AsyncSession whose execute() → scalars().all() returns *rows*.
    Also patches verify_audit_chain to return integrous result.
    """
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = rows

    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = 0

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result_mock)
    return db


# ---------------------------------------------------------------------------
# 1. Empty ledger — zero entries, valid structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_ledger_empty():
    db = _mock_db_for_ledger([])

    with patch(
        "services.audit.compliance.verify_audit_chain",
        new=AsyncMock(return_value={"is_integrous": True, "violations": [], "processed_count": 0}),
    ):
        bundle = await generate_tool_call_ledger(
            db,
            tenant_id=_TENANT_ID,
            start_date=_PERIOD_START,
            end_date=_PERIOD_END,
        )

    assert bundle["report_type"] == "tool_call_ledger"
    assert bundle["tenant_id"] == str(_TENANT_ID)
    assert bundle["agent_id"] == "all"
    assert bundle["total_calls"] == 0
    assert bundle["entries"] == []
    assert isinstance(bundle["by_decision"], dict)
    assert isinstance(bundle["by_tool"], dict)
    assert "generated_at" in bundle
    assert "period" in bundle


# ---------------------------------------------------------------------------
# 2. Ledger aggregates by_decision and by_tool correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_ledger_aggregates_correctly():
    rows = [
        _make_row(decision="allow", tool="read_file"),
        _make_row(decision="allow", tool="read_file"),
        _make_row(decision="deny",  tool="write_file"),
        _make_row(decision="deny",  tool="read_file"),
        _make_row(decision="escalate", tool="exec_cmd"),
    ]
    db = _mock_db_for_ledger(rows)

    with patch(
        "services.audit.compliance.verify_audit_chain",
        new=AsyncMock(return_value={"is_integrous": True, "violations": [], "processed_count": 5}),
    ):
        bundle = await generate_tool_call_ledger(db, tenant_id=_TENANT_ID)

    assert bundle["total_calls"] == 5
    assert bundle["by_decision"]["allow"] == 2
    assert bundle["by_decision"]["deny"] == 2
    assert bundle["by_decision"]["escalate"] == 1
    assert bundle["by_tool"]["read_file"] == 3
    assert bundle["by_tool"]["write_file"] == 1
    assert bundle["by_tool"]["exec_cmd"] == 1
    assert len(bundle["entries"]) == 5
    # Every entry has the required fields
    for entry in bundle["entries"]:
        for key in ("id", "timestamp", "agent_id", "tool", "decision", "reason", "event_hash", "request_id"):
            assert key in entry, f"entry missing key: {key}"


# ---------------------------------------------------------------------------
# 3. EU AI Act bundle has all required sections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eu_ai_act_bundle_structure():
    tool_rows = [
        _make_row(decision="allow", tool="read_file"),
        _make_row(decision="deny",  tool="write_file"),
    ]

    # DB mock: first call returns tool rows, subsequent calls return empty
    result_tool = MagicMock()
    result_tool.scalars.return_value.all.return_value = tool_rows

    result_empty = MagicMock()
    result_empty.scalars.return_value.all.return_value = []

    scalar_count = MagicMock()
    scalar_count.scalar_one_or_none.return_value = 1

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            result_tool,   # tool call query (Article 13)
            scalar_count,  # escalation count (Article 61)
            result_empty,  # anomaly log query (Article 61)
            result_tool,   # decision audit sample
        ]
    )

    with patch(
        "services.audit.compliance.verify_audit_chain",
        new=AsyncMock(return_value={"is_integrous": True, "violations": [], "processed_count": 2}),
    ):
        bundle = await generate_eu_ai_act_bundle(
            db,
            tenant_id=_TENANT_ID,
            period_start=_PERIOD_START,
            period_end=_PERIOD_END,
        )

    assert bundle["report_type"] == "eu_ai_act_bundle"
    assert bundle["framework"] == "EU AI Act"
    assert "articles_covered" in bundle
    assert "system_description" in bundle
    assert "tool_call_summary" in bundle
    assert "decision_audit" in bundle
    assert "anomaly_log" in bundle
    assert "integrity_proof_reference" in bundle

    # System description is not empty
    assert bundle["system_description"]["system_name"]
    # Integrity reference points to real endpoints
    ref = bundle["integrity_proof_reference"]
    assert "verify_endpoint" in ref
    assert "receipt_endpoint" in ref


# ---------------------------------------------------------------------------
# 4. SIEM Splunk payload has correct structure
# ---------------------------------------------------------------------------


def test_siem_splunk_format():
    from services.audit.siem import SIEMEvent, SplunkHECForwarder

    event = SIEMEvent(
        timestamp="2026-05-01T12:00:00+00:00",
        tenant_id=str(_TENANT_ID),
        agent_id=str(_AGENT_A),
        action="execute_tool",
        tool="read_file",
        decision="allow",
        reason=None,
        risk_score=0.25,
        request_id="req-abc-123",
        event_hash="a" * 64,
    )

    fwd = SplunkHECForwarder(hec_url="https://splunk.example.com:8088/services/collector", hec_token="tok123")
    payload = fwd._build_payload(event)

    # Required Splunk HEC fields
    assert payload["host"] == "acp"
    assert payload["source"] == "acp:audit"
    assert payload["sourcetype"] == "acp:governance"
    assert "time" in payload
    assert isinstance(payload["time"], float)

    # Event body contains the SIEMEvent fields
    evt = payload["event"]
    assert evt["tenant_id"] == str(_TENANT_ID)
    assert evt["agent_id"] == str(_AGENT_A)
    assert evt["action"] == "execute_tool"
    assert evt["tool"] == "read_file"
    assert evt["decision"] == "allow"
    assert evt["risk_score"] == 0.25
    assert evt["request_id"] == "req-abc-123"
    assert evt["event_hash"] == "a" * 64


# ---------------------------------------------------------------------------
# 5. SIEM Datadog payload has correct structure
# ---------------------------------------------------------------------------


def test_siem_datadog_format():
    from services.audit.siem import DatadogForwarder, SIEMEvent

    event = SIEMEvent(
        timestamp="2026-05-01T12:00:00+00:00",
        tenant_id=str(_TENANT_ID),
        agent_id=str(_AGENT_A),
        action="execute_tool",
        tool="exec_cmd",
        decision="deny",
        reason="path traversal detected",
        risk_score=0.95,
        request_id="req-xyz-789",
        event_hash="b" * 64,
    )

    fwd = DatadogForwarder(
        logs_url="https://http-intake.logs.datadoghq.com/api/v2/logs",
        api_key="dd-api-key-test",
    )
    payload = fwd._build_payload(event)

    assert isinstance(payload, list)
    assert len(payload) == 1
    item = payload[0]

    assert item["ddsource"] == "acp"
    assert str(_TENANT_ID) in item["ddtags"]
    assert "deny" in item["ddtags"]
    assert item["hostname"] == "acp-audit"
    assert item["service"] == "acp-governance"
    # Message is valid JSON containing the event
    msg_dict = json.loads(item["message"])
    assert msg_dict["decision"] == "deny"
    assert msg_dict["risk_score"] == 0.95
    assert msg_dict["tool"] == "exec_cmd"


# ---------------------------------------------------------------------------
# 6. export_bundle_as_json writes .json and .sha256 files
# ---------------------------------------------------------------------------


def test_export_bundle_writes_checksum(tmp_path: Path):
    bundle: dict[str, Any] = {
        "report_type": "tool_call_ledger",
        "tenant_id": str(_TENANT_ID),
        "generated_at": _NOW.isoformat(),
        "total_calls": 42,
        "entries": [],
    }

    out_path = tmp_path / "test_bundle.json"
    result = export_bundle_as_json(bundle, out_path)

    # Returns the .json path
    assert result == out_path
    assert out_path.exists()

    # SHA-256 sidecar exists
    checksum_path = out_path.with_suffix(".json.sha256")
    assert checksum_path.exists()

    # Verify the checksum is correct
    json_bytes = out_path.read_bytes()
    expected_digest = hashlib.sha256(json_bytes).hexdigest()
    checksum_content = checksum_path.read_text(encoding="utf-8")
    assert expected_digest in checksum_content
    assert "test_bundle.json" in checksum_content

    # JSON is valid and round-trips correctly
    loaded = json.loads(json_bytes)
    assert loaded["report_type"] == "tool_call_ledger"
    assert loaded["total_calls"] == 42
