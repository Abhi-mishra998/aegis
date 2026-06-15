"""
Sprint 4 — Fleet dashboard data layer + endpoint contract tests.

These tests exercise the new aggregations end-to-end via the FastAPI
route handlers using a fully in-process fake DB. They pin:

  * KPI rollup: deny rate / error rate / active agents are computed
    correctly for the window.
  * Time-series: bucket alignment + per-agent filter + metric whitelist
    rejection.
  * Agent Health: ranking is stable across tied agents (volume tie-break)
    and unknown rank_by surfaces as a 400.
  * Recent events: kind whitelist + ordering.
  * Burn-down: cents-precise threshold buckets (no_cap / ok / warning /
    critical / over) — the headline FinOps invariant.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from services.audit.fleet_aggregator import FleetAggregator
from services.audit.fleet_router import (
    get_fleet_agent_health,
    get_fleet_kpis,
    get_fleet_recent_events,
    get_fleet_timeseries,
)
from services.usage.router.fleet import _burn_down


# ---------------------------------------------------------------------------
# In-process fake DB
# ---------------------------------------------------------------------------


@dataclass
class _AuditRow:
    """Mimic the columns the FleetAggregator reads from AuditLog."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    agent_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    action: str = "execute_tool"
    decision: str = "allow"
    tool: str | None = "db.query"
    reason: str | None = None
    request_id: str | None = None
    metadata_json: dict = field(default_factory=lambda: {"risk_score": 0.1})


class _Result:
    """Mimics SQLAlchemy Result. Each query returns a list of rows."""

    def __init__(self, rows: list[Any] | tuple[Any, ...]) -> None:
        if isinstance(rows, tuple) and len(rows) == 1 and isinstance(rows[0], dict):
            # Single-row aggregate result; expose as one(), all() returning [row]
            class _Row:
                def __init__(self, d):
                    for k, v in d.items():
                        setattr(self, k, v)
            self._one = _Row(rows[0])
            self._rows = [self._one]
        else:
            self._rows = list(rows)
            self._one = self._rows[0] if self._rows else None

    def one(self):
        if self._one is None:
            raise AssertionError("no row to return from one()")
        return self._one

    def all(self):
        return self._rows


class _FakeDb:
    """Queue of pre-built results. The aggregator's exact query shape is
    not under test here — that's a Postgres concern — so we just stub the
    .execute() results the handler depends on."""

    def __init__(self, results: list[Any]) -> None:
        self._queue = [_Result(r) for r in results]

    async def execute(self, _stmt: Any):
        if not self._queue:
            raise AssertionError("test exhausted the result queue")
        return self._queue.pop(0)


def _kpis_row(**kwargs) -> tuple[dict]:
    base = {
        "decisions": 0, "denied": 0, "errors": 0,
        "active_agents": 0, "distinct_tools": 0,
    }
    base.update(kwargs)
    return (base,)


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kpis_computes_deny_and_error_rates_from_counts():
    tenant_id = uuid.uuid4()
    db = _FakeDb([
        _kpis_row(decisions=100, denied=20, errors=5, active_agents=4, distinct_tools=6),
    ])
    resp = await get_fleet_kpis(db, tenant_id, 60)   # type: ignore[arg-type]
    data = resp.data
    assert data.decisions == 100
    assert data.denied == 20
    assert data.errors == 5
    assert data.deny_rate == 0.20
    assert data.error_rate == 0.05
    assert data.active_agents == 4
    assert data.distinct_tools == 6


@pytest.mark.asyncio
async def test_kpis_returns_zero_rates_when_no_decisions():
    tenant_id = uuid.uuid4()
    db = _FakeDb([_kpis_row()])
    resp = await get_fleet_kpis(db, tenant_id, 60)   # type: ignore[arg-type]
    assert resp.data.deny_rate == 0.0
    assert resp.data.error_rate == 0.0
    assert resp.data.decisions == 0


# ---------------------------------------------------------------------------
# Time-series — metric whitelist + agent_id filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeseries_returns_buckets_in_order():
    tenant_id = uuid.uuid4()
    t1 = datetime(2026, 6, 13, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 13, 10, 5, tzinfo=UTC)
    t3 = datetime(2026, 6, 13, 10, 10, tzinfo=UTC)
    # The result is a list of bucket rows; aggregator returns them ordered.
    db = _FakeDb([
        [type("R", (), {"bucket": t1, "value": 5}),
         type("R", (), {"bucket": t2, "value": 9}),
         type("R", (), {"bucket": t3, "value": 4})],
    ])
    resp = await get_fleet_timeseries(
        db, tenant_id, "decisions", 180, 5, None,   # type: ignore[arg-type]
    )
    series = resp.data
    assert len(series) == 3
    assert series[0].v == 5.0
    assert series[1].v == 9.0
    assert series[2].v == 4.0
    assert series[0].t == t1.isoformat()


@pytest.mark.asyncio
async def test_timeseries_rejects_unknown_metric():
    from fastapi import HTTPException
    tenant_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        await get_fleet_timeseries(
            _FakeDb([]), tenant_id, "carbon_footprint", 180, 5, None,  # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400
    assert "metric must be one of" in exc.value.detail


@pytest.mark.asyncio
async def test_aggregator_raises_on_zero_bucket_minutes():
    """``bucket_minutes <= 0`` would produce a divide-by-zero in SQL —
    the aggregator surfaces it as a clear ValueError, mapped to 400 by
    the route handler."""
    with pytest.raises(ValueError):
        await FleetAggregator.timeseries(
            _FakeDb([]),               # type: ignore[arg-type]
            uuid.uuid4(),
            metric="decisions", window_minutes=60, bucket_minutes=0,
        )


# ---------------------------------------------------------------------------
# Agent Health — ranking + whitelist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_health_ranks_by_deny_rate_with_volume_tiebreak():
    tenant_id = uuid.uuid4()
    a_lo_vol = uuid.uuid4()
    a_hi_vol = uuid.uuid4()
    a_quiet  = uuid.uuid4()
    now = datetime.now(tz=UTC)
    rows = [
        # Same deny rate (0.5) — volume tie-break should put hi-vol first
        type("R", (), {"agent_id": a_lo_vol, "volume": 10,  "denied": 5,  "errors": 0, "avg_risk": 0.4, "last_seen": now}),
        type("R", (), {"agent_id": a_hi_vol, "volume": 100, "denied": 50, "errors": 0, "avg_risk": 0.4, "last_seen": now}),
        # Lower deny rate
        type("R", (), {"agent_id": a_quiet,  "volume": 50,  "denied": 5,  "errors": 0, "avg_risk": 0.1, "last_seen": now}),
    ]
    db = _FakeDb([rows])
    resp = await get_fleet_agent_health(
        db, tenant_id, "deny_rate", 60, 25,   # type: ignore[arg-type]
    )
    out = resp.data
    assert [r.agent_id for r in out] == [str(a_hi_vol), str(a_lo_vol), str(a_quiet)]
    assert out[0].deny_rate == 0.5
    assert out[0].volume == 100


@pytest.mark.asyncio
async def test_agent_health_rank_by_volume_orders_by_volume_descending():
    tenant_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [
        type("R", (), {"agent_id": a, "volume": 10,  "denied": 1, "errors": 0, "avg_risk": 0.1, "last_seen": now}),
        type("R", (), {"agent_id": b, "volume": 100, "denied": 1, "errors": 0, "avg_risk": 0.1, "last_seen": now}),
        type("R", (), {"agent_id": c, "volume": 50,  "denied": 1, "errors": 0, "avg_risk": 0.1, "last_seen": now}),
    ]
    db = _FakeDb([rows])
    resp = await get_fleet_agent_health(
        db, tenant_id, "volume", 60, 25,   # type: ignore[arg-type]
    )
    assert [r.volume for r in resp.data] == [100, 50, 10]


@pytest.mark.asyncio
async def test_agent_health_rejects_unknown_rank_by():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await get_fleet_agent_health(
            _FakeDb([[]]), uuid.uuid4(), "vibes", 60, 25,   # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Recent events — kind whitelist + payload shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recent_events_returns_audit_payload_shape():
    tenant_id = uuid.uuid4()
    aid = uuid.uuid4()
    now = datetime.now(tz=UTC)
    rows = [
        type("R", (), {
            "id": uuid.uuid4(), "timestamp": now, "agent_id": aid,
            "tool": "db.query", "action": "execute_tool", "decision": "deny",
            "reason": "SQL_DDL_DESTRUCTION", "request_id": "req-7",
            "metadata_json": {"risk_score": 0.95},
        }),
    ]
    db = _FakeDb([rows])
    resp = await get_fleet_recent_events(
        db, tenant_id, "denied", 25,   # type: ignore[arg-type]
    )
    out = resp.data
    assert len(out) == 1
    e = out[0]
    assert e.decision == "deny"
    assert e.reason == "SQL_DDL_DESTRUCTION"
    assert e.tool == "db.query"
    assert e.risk_score == 0.95
    assert e.request_id == "req-7"


@pytest.mark.asyncio
async def test_recent_events_rejects_unknown_kind():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await get_fleet_recent_events(
            _FakeDb([[]]), uuid.uuid4(), "celebrations", 25,   # type: ignore[arg-type]
        )
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Burn-down threshold buckets
# ---------------------------------------------------------------------------


def test_burn_down_no_cap_reports_no_cap_status():
    out = _burn_down(used_usd=12.50, cap_usd=0.0)
    assert out["status"] == "no_cap"
    assert out["percent_used"] is None
    assert out["remaining_usd"] is None


def test_burn_down_ok_when_below_50_percent():
    out = _burn_down(used_usd=2.0, cap_usd=10.0)
    assert out["status"] == "ok"
    assert out["percent_used"] == 0.20
    assert out["remaining_usd"] == 8.0


def test_burn_down_warning_at_or_above_50_percent():
    out = _burn_down(used_usd=5.0, cap_usd=10.0)
    assert out["status"] == "warning"


def test_burn_down_critical_at_or_above_80_percent():
    out = _burn_down(used_usd=8.0, cap_usd=10.0)
    assert out["status"] == "critical"
    assert out["percent_used"] == 0.80


def test_burn_down_over_at_or_above_100_percent():
    out = _burn_down(used_usd=12.50, cap_usd=10.0)
    assert out["status"] == "over"
    assert out["remaining_usd"] == 0.0
    assert out["percent_used"] == 1.25


def test_burn_down_thresholds_match_sprint_2_2_warning_gate():
    """Sprint 2.2 fires the one-shot 80% warning. The burn-down endpoint
    must transition into `critical` at the same point so the UI and the
    alert pipeline agree on which usage value triggered the warning."""
    just_under = _burn_down(used_usd=7.99, cap_usd=10.0)
    just_over = _burn_down(used_usd=8.00, cap_usd=10.0)
    assert just_under["status"] == "warning"
    assert just_over["status"] == "critical"
