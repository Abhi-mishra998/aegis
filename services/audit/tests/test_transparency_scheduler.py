"""Unit tests for the transparency scheduler.

These exercise the module-level logic without booting Postgres. We mock the
session factory and the persistence/computation primitives.
"""
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.audit.transparency_scheduler import (
    BACKFILL_WINDOW_DAYS,
    _commit_one,
    _one_pass,
    run_transparency_scheduler,
)


@pytest.fixture
def fake_session():
    """A mock async context manager that yields a mock session."""
    s = AsyncMock()
    s.__aenter__.return_value = s
    s.__aexit__.return_value = None
    return s


@pytest.fixture
def fake_factory(fake_session):
    def _factory():
        return fake_session
    return _factory


@pytest.mark.asyncio
async def test_one_pass_commits_each_missing_pair(fake_factory):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    yesterday = (datetime.now(UTC).date() - timedelta(days=1))
    pairs = [(tenant_a, yesterday), (tenant_b, yesterday)]

    with patch(
        "services.audit.transparency_scheduler._missing_pairs",
        new=AsyncMock(return_value=pairs),
    ), patch(
        "services.audit.transparency_scheduler._commit_one",
        new=AsyncMock(),
    ) as commit_mock:
        n = await _one_pass(fake_factory)
        assert n == 2
        assert commit_mock.await_count == 2


@pytest.mark.asyncio
async def test_one_pass_continues_through_per_pair_failures(fake_factory):
    pairs = [(uuid.uuid4(), date.today() - timedelta(days=1)) for _ in range(3)]

    call_count = {"n": 0}

    async def flaky(db, tenant_id, day):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("simulated DB blip")

    with patch(
        "services.audit.transparency_scheduler._missing_pairs",
        new=AsyncMock(return_value=pairs),
    ), patch(
        "services.audit.transparency_scheduler._commit_one",
        new=flaky,
    ):
        n = await _one_pass(fake_factory)
        # One of three failed, two succeeded.
        assert n == 2


@pytest.mark.asyncio
async def test_scheduler_cancellation_exits_cleanly(fake_factory):
    import asyncio
    with patch(
        "services.audit.transparency_scheduler._one_pass",
        new=AsyncMock(return_value=0),
    ), patch(
        "services.audit.transparency_scheduler.SCHEDULER_INTERVAL_SECONDS",
        0.01,
    ):
        task = asyncio.create_task(run_transparency_scheduler(fake_factory))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # Should not raise; clean shutdown
        assert task.done()


def test_backfill_window_is_reasonable():
    # Sanity: don't let someone set the window to 0 or 365 silently.
    assert 1 <= BACKFILL_WINDOW_DAYS <= 30
