"""Regression: generate_eu_ai_act_bundle must not load every audit row
in the period into Python memory.

The prior implementation did `list(scalars().all())` on a query with no
LIMIT — on a 1M-actions/day tenant (the §12.1 reference workload) a
year-long compliance window is 365M rows, an OOM waiting for a
regulator request.

Test strategy: mock the async db.execute to return each aggregate the
new `_tally_execute_tool_calls_sql` expects, then assert the returned
tuple has the right shape + values. No DB needed. Combined with a
whitebox check that the caller (`generate_eu_ai_act_bundle`) invokes
the SQL path, this locks in the fix.
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_generate_eu_ai_act_bundle_uses_sql_aggregator():
    """Whitebox: the source of generate_eu_ai_act_bundle must call the
    SQL aggregator and MUST NOT re-introduce the unbounded
    `list(...scalars().all())` pattern on the tool-rows query.
    A regression that reverts to the python-tally path fails this."""
    from services.audit import compliance as _cmod
    src = inspect.getsource(_cmod.generate_eu_ai_act_bundle)
    assert "_tally_execute_tool_calls_sql" in src, (
        "generate_eu_ai_act_bundle no longer calls the SQL aggregator "
        "— the unbounded-row DoS is back"
    )
    # The old python-side full-row load must NOT reappear on the tool_q.
    assert "tool_rows: list[AuditLog] = list(tool_result.scalars().all())" not in src


@pytest.mark.asyncio
async def test_sql_aggregator_processes_mocked_results():
    """Feed the aggregator canned db.execute results in the exact shape
    Postgres returns, assert it packs the tuple correctly."""
    from services.audit.compliance import _tally_execute_tool_calls_sql

    tid = uuid.uuid4()
    first_uuid = uuid.uuid4()
    last_uuid = uuid.uuid4()

    # 5 db.execute calls in order: total count, by_tool group, by_decision
    # group, first-id, last-id. Each returns a MagicMock with the shape the
    # aggregator peels off (`.scalar_one() / .all() / .scalar_one_or_none()`).
    calls: list = []

    def _mk_total_result():
        r = MagicMock()
        r.scalar_one.return_value = 42
        return r

    def _mk_group_result(pairs):
        r = MagicMock()
        r.all.return_value = pairs
        return r

    def _mk_scalar_result(val):
        r = MagicMock()
        r.scalar_one_or_none.return_value = val
        return r

    scripted = [
        _mk_total_result(),
        _mk_group_result([("delete_record", 30), ("send_wire", 12)]),
        _mk_group_result([("allow", 35), ("deny", 5), ("escalate", 2)]),
        _mk_scalar_result(first_uuid),
        _mk_scalar_result(last_uuid),
    ]

    async def _fake_execute(_stmt):
        calls.append(_stmt)
        return scripted.pop(0)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_fake_execute)

    total, by_tool, by_decision, first_id, last_id = (
        await _tally_execute_tool_calls_sql(
            db, tid,
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, tzinfo=UTC),
        )
    )

    assert total == 42
    assert by_tool == {"delete_record": 30, "send_wire": 12}
    assert by_decision == {"allow": 35, "deny": 5, "escalate": 2}
    assert first_id == str(first_uuid)
    assert last_id == str(last_uuid)

    # The aggregator issued exactly 5 queries (COUNT, GROUP BY tool,
    # GROUP BY decision, first-id, last-id). Anything more means it
    # accidentally added a row-fetching query — which is what we're
    # trying to prevent.
    assert db.execute.await_count == 5


@pytest.mark.asyncio
async def test_sql_aggregator_handles_empty_period():
    """No matching rows in the window: total=0, empty dicts, first/last=None."""
    from services.audit.compliance import _tally_execute_tool_calls_sql

    def _mk_total_result():
        r = MagicMock()
        r.scalar_one.return_value = 0
        return r

    def _mk_group_result():
        r = MagicMock()
        r.all.return_value = []
        return r

    def _mk_scalar_result_none():
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        return r

    scripted = [
        _mk_total_result(),
        _mk_group_result(),
        _mk_group_result(),
        _mk_scalar_result_none(),
        _mk_scalar_result_none(),
    ]

    async def _fake_execute(_stmt):
        return scripted.pop(0)

    db = MagicMock()
    db.execute = AsyncMock(side_effect=_fake_execute)

    total, by_tool, by_decision, first_id, last_id = (
        await _tally_execute_tool_calls_sql(
            db, uuid.uuid4(),
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 12, 31, tzinfo=UTC),
        )
    )

    assert total == 0
    assert by_tool == {}
    assert by_decision == {}
    assert first_id is None
    assert last_id is None
