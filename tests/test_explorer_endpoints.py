"""
Sprint 3.3 + 3.5 — tests for the Decision Explorer + Session Explorer
backend endpoints.

These tests exercise the route handlers directly with a synthetic
async database session so they run without a live Postgres. They pin:

  * `_step_type_to_stage` maps the legacy ``step_type`` vocabulary to the
    canonical 11-stage names.
  * Decision Explorer returns nodes in pipeline order, edges between
    consecutive stages with the upstream signal label, and the cost +
    token totals from snapshots.
  * Session Explorer groups timelines by ``session_id``, computes
    risk-trajectory sparklines, and skips timelines without a session.
  * Missing data surfaces as 404 (not 500 or silent empty payload).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from services.flight_recorder.router import (
    _step_type_to_stage,
    get_decision_graph,
    get_session,
    list_sessions,
)


# ---------------------------------------------------------------------------
# Test doubles — keep the test wholly in-process
# ---------------------------------------------------------------------------


@dataclass
class _Step:
    """Mimic ``ExecutionStep`` for the explorer-graph rollup."""
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    timeline_id: uuid.UUID = field(default_factory=uuid.uuid4)
    step_index: int = 0
    step_type: str = "decision"
    status: str = "ok"
    latency_ms: int | None = None
    risk_score: float | None = None
    summary: str | None = None
    payload: dict = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _Snapshot:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    timeline_id: uuid.UUID = field(default_factory=uuid.uuid4)
    step_index: int = 0
    snapshot: dict = field(default_factory=dict)
    tokens_in: int | None = None
    tokens_out: int | None = None
    captured_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _Timeline:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    org_id: uuid.UUID = field(default_factory=uuid.uuid4)
    request_id: str = "req-x"
    session_id: str | None = None
    agent_id: uuid.UUID | None = None
    tool: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    completed_at: datetime | None = None
    duration_ms: int | None = None
    final_decision: str | None = None
    final_risk: float | None = None
    status: str = "ok"
    metadata_json: dict = field(default_factory=dict)


class _FakeResult:
    """Returns from db.execute(...) — mimics SQLAlchemy's Result for the
    handlers' use of .scalar_one_or_none() / .scalars().all()."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalars(self) -> Any:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeDb:
    """Queue-of-statements stand-in for ``AsyncSession``. The handler
    issues a deterministic sequence of ``await db.execute(stmt)`` calls;
    we feed it pre-built ``_FakeResult`` objects in that order."""

    def __init__(self, results: list[list[Any]]) -> None:
        self._queue = [_FakeResult(r) for r in results]

    async def execute(self, _stmt: Any) -> _FakeResult:
        if not self._queue:
            raise AssertionError("test exhausted the result queue")
        return self._queue.pop(0)


# ---------------------------------------------------------------------------
# Stage mapping
# ---------------------------------------------------------------------------


def test_step_type_to_stage_uses_explicit_when_present():
    assert _step_type_to_stage("anything", {"stage": "behavior"}) == "behavior"


def test_step_type_to_stage_maps_legacy_step_types():
    assert _step_type_to_stage("prompt", {}) == "inference_proxy"
    assert _step_type_to_stage("tool_call", {}) == "execution"
    assert _step_type_to_stage("policy", {}) == "policy"
    assert _step_type_to_stage("decision", {}) == "decision"
    assert _step_type_to_stage("retry", {}) == "execution"
    assert _step_type_to_stage("failure", {}) == "execution"
    assert _step_type_to_stage("unknown_type", {}) == "decision"


def test_step_type_to_stage_explicit_overrides_legacy():
    """An explicit ``stage`` in payload wins even if step_type maps differently."""
    assert _step_type_to_stage("tool_call", {"stage": "kill_switch"}) == "kill_switch"


def test_step_type_to_stage_rejects_unknown_explicit_stage():
    """A bad explicit ``stage`` falls through to step_type mapping rather
    than corrupting the graph with a non-canonical name."""
    assert _step_type_to_stage("policy", {"stage": "elvish_runes"}) == "policy"


# ---------------------------------------------------------------------------
# Decision Explorer graph endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_graph_orders_stages_canonically():
    tenant_id = uuid.uuid4()
    timeline = _Timeline(
        tenant_id=tenant_id, request_id="req-7",
        duration_ms=42, started_at=datetime.now(tz=UTC),
    )
    # Insert steps out of canonical order to prove the response is sorted.
    steps = [
        _Step(timeline_id=timeline.id, step_index=2,
              step_type="decision", risk_score=0.4,
              payload={"outcome": "allow"}, tenant_id=tenant_id,
              summary="combined-signal allow"),
        _Step(timeline_id=timeline.id, step_index=0,
              step_type="policy", risk_score=0.2,
              payload={"outcome": "allow"}, tenant_id=tenant_id,
              summary="OPA allow"),
        _Step(timeline_id=timeline.id, step_index=1,
              step_type="prompt", risk_score=0.1,
              payload={"stage": "inference_proxy", "outcome": "no_findings"},
              tenant_id=tenant_id, summary="inj-classifier clean"),
    ]
    snapshots = [
        _Snapshot(timeline_id=timeline.id, tokens_in=200, tokens_out=80,
                  tenant_id=tenant_id),
    ]
    db = _FakeDb([
        [timeline],   # timeline lookup
        steps,        # ExecutionStep query
        snapshots,    # ExecutionSnapshot query
    ])

    resp = await get_decision_graph("req-7", tenant_id, db)  # type: ignore[arg-type]

    data = resp.data
    assert [n.stage for n in data.nodes] == ["inference_proxy", "policy", "decision"]
    assert [e.source for e in data.edges] == [
        "stage:inference_proxy", "stage:policy",
    ]
    assert [e.target for e in data.edges] == ["stage:policy", "stage:decision"]
    # Token + cost rollups land on the trace-overview view.
    assert data.tokens_in == 200
    assert data.tokens_out == 80
    assert data.estimated_usd == pytest.approx((200 + 80) / 1000 * 0.50)


@pytest.mark.asyncio
async def test_decision_graph_returns_404_when_request_not_found():
    db = _FakeDb([[]])
    with pytest.raises(HTTPException) as exc:
        await get_decision_graph("never-existed", uuid.uuid4(), db)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_decision_graph_collapses_repeated_stage_to_highest_risk():
    tenant_id = uuid.uuid4()
    timeline = _Timeline(tenant_id=tenant_id, request_id="req-8")
    steps = [
        _Step(timeline_id=timeline.id, step_index=0, step_type="policy",
              risk_score=0.3, payload={"outcome": "allow"}, tenant_id=tenant_id,
              summary="early policy probe"),
        _Step(timeline_id=timeline.id, step_index=1, step_type="policy",
              risk_score=0.85, payload={"outcome": "deny"}, tenant_id=tenant_id,
              summary="late policy reload"),
    ]
    db = _FakeDb([[timeline], steps, []])

    resp = await get_decision_graph("req-8", tenant_id, db)  # type: ignore[arg-type]
    nodes = resp.data.nodes
    assert len(nodes) == 1
    assert nodes[0].stage == "policy"
    assert nodes[0].risk_score == pytest.approx(0.85)
    assert nodes[0].outcome == "deny"


# ---------------------------------------------------------------------------
# Session Explorer endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_groups_by_session_id_and_sorts_by_recency():
    tenant_id = uuid.uuid4()
    now = datetime.now(tz=UTC)

    # Session A — two old timelines.
    t_a1 = _Timeline(tenant_id=tenant_id, request_id="a1", session_id="sess-a",
                     started_at=now - timedelta(hours=2), final_risk=0.2,
                     agent_id=uuid.uuid4(), tool="db.query")
    t_a2 = _Timeline(tenant_id=tenant_id, request_id="a2", session_id="sess-a",
                     started_at=now - timedelta(hours=1, minutes=30),
                     final_risk=0.6, agent_id=uuid.uuid4(), tool="db.execute")
    # Session B — one very recent timeline (should sort first).
    t_b = _Timeline(tenant_id=tenant_id, request_id="b1", session_id="sess-b",
                    started_at=now - timedelta(minutes=10), final_risk=0.1,
                    agent_id=uuid.uuid4(), tool="crm.lookup")
    # Pre-Sprint-3 row without a session_id — must be filtered out.
    t_no_session = _Timeline(tenant_id=tenant_id, request_id="x1",
                             session_id=None, final_risk=0.05)

    # The list_sessions handler filters NULL session_id at the SQL layer;
    # the fake DB just returns the rows that would have matched.
    db = _FakeDb([[t_a1, t_a2, t_b]])

    resp = await list_sessions(tenant_id, db, 1440, 100)  # type: ignore[arg-type]
    sessions = resp.data
    assert [s.session_id for s in sessions] == ["sess-b", "sess-a"]
    sess_a = next(s for s in sessions if s.session_id == "sess-a")
    assert sess_a.decision_count == 2
    assert sess_a.distinct_agents == 2
    assert sess_a.distinct_tools == 2
    assert sess_a.max_risk == pytest.approx(0.6)
    assert sess_a.risk_trajectory == [0.2, 0.6]


@pytest.mark.asyncio
async def test_get_session_returns_404_when_no_matching_timelines():
    db = _FakeDb([[]])
    with pytest.raises(HTTPException) as exc:
        await get_session("nope", uuid.uuid4(), db)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_session_returns_chronological_timelines_and_trajectory():
    tenant_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    t1 = _Timeline(tenant_id=tenant_id, request_id="r1", session_id="sess-x",
                   started_at=now - timedelta(minutes=20), final_risk=0.1)
    t2 = _Timeline(tenant_id=tenant_id, request_id="r2", session_id="sess-x",
                   started_at=now - timedelta(minutes=10), final_risk=0.4)
    t3 = _Timeline(tenant_id=tenant_id, request_id="r3", session_id="sess-x",
                   started_at=now, final_risk=0.8)
    db = _FakeDb([[t1, t2, t3]])

    resp = await get_session("sess-x", tenant_id, db)  # type: ignore[arg-type]
    assert [t.request_id for t in resp.data.timelines] == ["r1", "r2", "r3"]
    assert resp.data.risk_trajectory == [0.1, 0.4, 0.8]
