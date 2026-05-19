"""Unit tests for the Flight Recorder lifecycle sprint (2026-05-15).

Covers:
* Worker `timeline_start` event now backfills tool/agent_id/metadata when the
  row was previously created from an out-of-order `step` event with no tool.
* Worker `timeline_start` is idempotent when the row already has tool set.
* Worker `timeline_end` event still finalises the row.
* Backfill helper `_infer_decision` / `_infer_tool` projections.
* Backfill `_process_timeline` recovers a stuck row with the expected shape.
* trust_emitter metric wiring: emit_timeline_start increments the open
  counter; emit_timeline_end increments the close counter (overall + status).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.maintenance import backfill_flight_timelines as bf
from services.flight_recorder import worker as fr_worker
from services.flight_recorder.models import ExecutionTimeline

# --------------------------------------------------------------------------- #
# Backfill helpers — pure functions                                           #
# --------------------------------------------------------------------------- #


class TestInferDecision:
    def test_ok_maps_to_allow(self):
        assert bf._infer_decision("ok", "policy") == "allow"

    def test_allow_passthrough(self):
        assert bf._infer_decision("allow", None) == "allow"

    def test_deny_maps_to_block(self):
        assert bf._infer_decision("deny", "policy") == "block"

    def test_block_passthrough(self):
        assert bf._infer_decision("block", "inference_proxy") == "block"

    def test_error_passthrough(self):
        assert bf._infer_decision("error", "failure") == "error"

    def test_pending_maps_to_escalate(self):
        assert bf._infer_decision("pending", "autonomy") == "escalate"

    def test_failure_step_type_when_status_missing(self):
        assert bf._infer_decision(None, "failure") == "error"

    def test_unknown_defaults_to_error(self):
        # Conservative default: unknown rows should look anomalous in dashboards
        # so an operator goes looking. "error" is the loudest legal label.
        assert bf._infer_decision(None, "policy") == "error"
        assert bf._infer_decision("weird", None) == "error"


class TestInferTool:
    def test_no_step_returns_none(self):
        assert bf._infer_tool(None) is None

    def test_payload_tool_wins(self):
        s = SimpleNamespace(payload={"tool": "read_file"}, step_type="policy")
        assert bf._infer_tool(s) == "read_file"

    def test_payload_tool_name_alias(self):
        s = SimpleNamespace(payload={"tool_name": "query"}, step_type="policy")
        assert bf._infer_tool(s) == "query"

    def test_no_payload_returns_none(self):
        # `step_type` is a phase label, not a tool; refuse to guess.
        s = SimpleNamespace(payload={}, step_type="policy")
        assert bf._infer_tool(s) is None

    def test_payload_tool_is_truncated_to_255(self):
        s = SimpleNamespace(payload={"tool": "x" * 1000}, step_type="policy")
        assert len(bf._infer_tool(s)) == 255


# --------------------------------------------------------------------------- #
# Backfill _process_timeline — exercises step → timeline projection           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_process_timeline_recovers_stuck_row(monkeypatch):
    """_process_timeline should compute tool/decision/duration from steps and
    issue exactly one UPDATE bound to the row's id + status='in_progress'."""
    tl_id = uuid.uuid4()
    started_at = datetime.now(tz=UTC) - timedelta(minutes=10)
    timeline = SimpleNamespace(
        id=tl_id, tool=None, started_at=started_at, status="in_progress",
        request_id="req-stuck-1",
    )

    s1 = SimpleNamespace(
        timeline_id=tl_id, step_index=0, step_type="inference_proxy",
        status="ok", payload={"tool": "read_file"},
        occurred_at=started_at + timedelta(milliseconds=50),
    )
    s2 = SimpleNamespace(
        timeline_id=tl_id, step_index=1, step_type="policy",
        status="ok", payload={"reasons": []},
        occurred_at=started_at + timedelta(milliseconds=250),
    )

    # Stub db.execute → returns an object with .scalars().all() → [s1, s2] for
    # the steps query; capture the UPDATE statement separately so we can
    # validate it.
    captured_updates: list = []
    steps_result = MagicMock()
    steps_result.scalars.return_value = MagicMock(all=MagicMock(return_value=[s1, s2]))

    class _FakeDb:
        async def execute(self, stmt):
            text = str(stmt).lower()
            if "update" in text:
                captured_updates.append(stmt)
                return None
            return steps_result

        async def commit(self):  # pragma: no cover — caller awaits commit
            return None

    summary = await bf._process_timeline(_FakeDb(), timeline, dry_run=False)

    assert summary["tool_after"] == "read_file"
    assert summary["final_decision"] == "allow"
    assert summary["step_count"] == 2
    assert summary["duration_ms"] >= 200
    assert len(captured_updates) == 1


@pytest.mark.asyncio
async def test_process_timeline_dry_run_does_not_update():
    """--dry-run must compute the recovery shape without issuing UPDATE."""
    tl_id = uuid.uuid4()
    started_at = datetime.now(tz=UTC) - timedelta(minutes=8)
    timeline = SimpleNamespace(
        id=tl_id, tool=None, started_at=started_at, status="in_progress",
        request_id="req-dryrun-1",
    )
    s = SimpleNamespace(
        timeline_id=tl_id, step_index=0, step_type="failure",
        status="deny", payload={"tool": "exec"},
        occurred_at=started_at + timedelta(milliseconds=10),
    )
    steps_result = MagicMock()
    steps_result.scalars.return_value = MagicMock(all=MagicMock(return_value=[s]))

    db_execute = AsyncMock(return_value=steps_result)
    fake_db = SimpleNamespace(execute=db_execute, commit=AsyncMock())

    summary = await bf._process_timeline(fake_db, timeline, dry_run=True)

    assert summary["final_decision"] == "block"
    assert summary["tool_after"] == "exec"
    # Exactly one query — the steps SELECT — and NO update.
    assert db_execute.await_count == 1


# --------------------------------------------------------------------------- #
# Worker — timeline_start backfills tool when row pre-existed from a step     #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_worker_timeline_start_backfills_tool_on_existing_row(monkeypatch):
    """The gateway emits emit_step BEFORE emit_timeline_start; Redis Streams
    can deliver them in either order. When a step lands first, the worker's
    `_get_or_create_timeline` creates the row with tool=None — the timeline_start
    handler MUST patch the row in-place rather than no-op."""
    tenant_id = uuid.uuid4()
    request_id = "req-out-of-order-1"
    agent_uuid = uuid.uuid4()

    # Pretend a step already created the row.
    pre_existing = ExecutionTimeline()
    pre_existing.id = uuid.uuid4()
    pre_existing.tenant_id = tenant_id
    pre_existing.org_id = tenant_id
    pre_existing.request_id = request_id
    pre_existing.tool = None        # ← the bug we're fixing
    pre_existing.agent_id = None
    pre_existing.metadata_json = {}
    pre_existing.status = "in_progress"

    # Stub session: scalar_one_or_none returns the pre-existing row.
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = pre_existing

    commits: list[bool] = []

    class _FakeDb:
        async def execute(self, stmt):
            return select_result

        async def commit(self):
            commits.append(True)

        def add(self, _):  # only for ON CONFLICT cases — not exercised here
            pass

    ev = {
        "kind":      "timeline_start",
        "tenant_id": str(tenant_id),
        "request_id": request_id,
        "agent_id":  str(agent_uuid),
        "tool":      "read_file",
        "metadata":  {"tier": "pro"},
    }

    await fr_worker._apply_event(_FakeDb(), ev)

    assert pre_existing.tool == "read_file", "tool was not backfilled"
    assert pre_existing.agent_id == agent_uuid, "agent_id was not backfilled"
    assert pre_existing.metadata_json == {"tier": "pro"}, "metadata was not backfilled"
    assert commits, "expected exactly one commit when fields were patched"


@pytest.mark.asyncio
async def test_worker_timeline_start_is_idempotent_when_row_already_has_tool():
    """Replays must not stomp existing values."""
    tenant_id = uuid.uuid4()
    request_id = "req-idem-1"

    existing = ExecutionTimeline()
    existing.id = uuid.uuid4()
    existing.tenant_id = tenant_id
    existing.org_id = tenant_id
    existing.request_id = request_id
    existing.tool = "read_file"
    existing.agent_id = uuid.uuid4()
    existing.metadata_json = {"tier": "pro"}
    existing.status = "in_progress"

    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = existing
    commits: list[bool] = []

    class _FakeDb:
        async def execute(self, _):
            return select_result

        async def commit(self):
            commits.append(True)

        def add(self, _):
            pass

    ev = {
        "kind":      "timeline_start",
        "tenant_id": str(tenant_id),
        "request_id": request_id,
        "agent_id":  str(uuid.uuid4()),  # different from existing
        "tool":      "delete",            # would clobber if not idempotent
        "metadata":  {"tier": "basic"},
    }
    await fr_worker._apply_event(_FakeDb(), ev)

    assert existing.tool == "read_file", "tool was clobbered by replay"
    assert existing.metadata_json == {"tier": "pro"}, "metadata was clobbered"
    # No commit needed when nothing changed.
    assert commits == []


# --------------------------------------------------------------------------- #
# trust_emitter — metric wiring                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_emit_timeline_start_end_increment_counters():
    from sdk.utils import (
        FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL,
        FLIGHT_TIMELINE_CLOSED_TOTAL,
        FLIGHT_TIMELINE_OPEN_TOTAL,
    )
    from services.gateway.trust_emitter import emit_timeline_end, emit_timeline_start

    fake_redis = AsyncMock()
    fake_redis.xadd = AsyncMock(return_value=b"1-0")

    before_open = FLIGHT_TIMELINE_OPEN_TOTAL._value.get()
    before_closed = FLIGHT_TIMELINE_CLOSED_TOTAL._value.get()
    before_ok = FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(status="ok")._value.get()
    before_failed = FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(status="failed")._value.get()

    await emit_timeline_start(
        fake_redis, tenant_id="t1", request_id="r1",
        agent_id="a1", tool="read_file",
    )
    await emit_timeline_end(
        fake_redis, tenant_id="t1", request_id="r1",
        final_decision="allow", final_risk=0.1, status="ok",
    )
    await emit_timeline_end(
        fake_redis, tenant_id="t1", request_id="r2",
        final_decision="block", final_risk=1.0, status="failed",
    )
    # Unknown status falls into the `failed` bucket per the cardinality cap.
    await emit_timeline_end(
        fake_redis, tenant_id="t1", request_id="r3",
        final_decision="error", final_risk=0.5, status="garbage",
    )

    assert FLIGHT_TIMELINE_OPEN_TOTAL._value.get() == before_open + 1
    assert FLIGHT_TIMELINE_CLOSED_TOTAL._value.get() == before_closed + 3
    assert FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(status="ok")._value.get() == before_ok + 1
    # Two `failed` increments (the "failed" call and the "garbage" coercion).
    assert FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(status="failed")._value.get() == before_failed + 2

    # xadd should have fired four times (one start + three ends).
    assert fake_redis.xadd.await_count == 4
