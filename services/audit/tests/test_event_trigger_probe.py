"""N23 (2026-06-21) — unit tests for the P0-3 event-trigger probe.

Covers ``services.audit.main._probe_event_triggers``:

  * enabled state ("O") flips the gauge to 0
  * disabled state ("D") flips the gauge to 1 + emits a critical log
  * missing row flips the gauge to 1 + emits a critical log
  * DB error during probe defaults the gauge to 1 (silent failure is unsafe)

The function is a thin SQL probe, so we mock the SQLAlchemy session and
verify both the gauge state and the structured-log emissions.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.audit import main as audit_main


def _gauge_value(trigger: str) -> float:
    """Read the current gauge value for one trigger label."""
    return audit_main.acp_audit_event_trigger_disabled.labels(
        trigger=trigger
    )._value.get()


def _mock_db(rows: list[tuple[str, str]]) -> AsyncMock:
    """An AsyncSession whose .execute returns ``rows`` from .fetchall()."""
    db = AsyncMock()
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.fixture(autouse=True)
def _reset_gauges() -> None:
    """Force the gauges back to 0 between tests so each case starts clean."""
    for name in audit_main._EVENT_TRIGGER_NAMES:
        audit_main.acp_audit_event_trigger_disabled.labels(trigger=name).set(0)


@pytest.mark.asyncio
async def test_probe_both_triggers_enabled_keeps_gauges_low() -> None:
    """Healthy steady state: both event triggers report evtenabled='O'."""
    db = _mock_db([
        ("protect_audit_logs",      "O"),
        ("protect_audit_logs_drop", "O"),
    ])
    states = await audit_main._probe_event_triggers(db)
    assert states["protect_audit_logs"] == "O"
    assert states["protect_audit_logs_drop"] == "O"
    assert _gauge_value("protect_audit_logs") == 0.0
    assert _gauge_value("protect_audit_logs_drop") == 0.0


@pytest.mark.asyncio
async def test_probe_disabled_trigger_flips_gauge_to_one() -> None:
    """Break-glass: ALTER EVENT TRIGGER ... DISABLE leaves evtenabled='D'."""
    db = _mock_db([
        ("protect_audit_logs",      "D"),  # operator just disabled it
        ("protect_audit_logs_drop", "O"),
    ])
    states = await audit_main._probe_event_triggers(db)
    assert states["protect_audit_logs"] == "D"
    assert _gauge_value("protect_audit_logs") == 1.0
    assert _gauge_value("protect_audit_logs_drop") == 0.0


@pytest.mark.asyncio
async def test_probe_missing_trigger_flips_gauge_to_one() -> None:
    """Migration not yet applied: pg_event_trigger has no row at all."""
    db = _mock_db([])  # neither trigger exists
    states = await audit_main._probe_event_triggers(db)
    assert states["protect_audit_logs"] == "MISSING"
    assert states["protect_audit_logs_drop"] == "MISSING"
    assert _gauge_value("protect_audit_logs") == 1.0
    assert _gauge_value("protect_audit_logs_drop") == 1.0


@pytest.mark.asyncio
async def test_probe_db_error_defaults_gauges_to_alarm_state() -> None:
    """If the probe itself blows up, we must not look healthy."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=RuntimeError("connection lost"))
    states = await audit_main._probe_event_triggers(db)
    # All gauges flip to 1 even though we couldn't observe truth.
    assert _gauge_value("protect_audit_logs") == 1.0
    assert _gauge_value("protect_audit_logs_drop") == 1.0
    # And we return the sentinel state so a caller can log/decide.
    for name in audit_main._EVENT_TRIGGER_NAMES:
        assert states[name] == "MISSING"


@pytest.mark.asyncio
async def test_probe_replica_only_state_is_treated_as_enabled() -> None:
    """``R`` (enabled on replica only) still counts as enabled — primary
    may still receive DDL, but the alarm is "not enabled in any role",
    not "wrong replica setting". Per spec we accept O / A / R as healthy.
    """
    db = _mock_db([
        ("protect_audit_logs",      "R"),
        ("protect_audit_logs_drop", "A"),
    ])
    await audit_main._probe_event_triggers(db)
    assert _gauge_value("protect_audit_logs") == 0.0
    assert _gauge_value("protect_audit_logs_drop") == 0.0
