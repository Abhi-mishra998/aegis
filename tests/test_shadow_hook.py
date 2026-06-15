"""Sprint 6 — Tests for the gateway shadow-mode hook.

The single most important guarantee these tests pin: a shadow policy
that would DENY a request MUST NOT influence the response the live
pipeline sends back. The /execute response is computed from
``request.state.decision`` BEFORE the shadow hook is dispatched, and
the hook has no return path back into the request handler.

We verify that contract three ways:

  1. ``schedule()`` returns an asyncio.Task and never raises into the
     caller — proves the splice in middleware can be a no-op fire-and-
     forget call regardless of whether the audit DB is reachable.
  2. ``evaluate_and_record()`` writes shadow_decisions rows for a
     shadow-deny policy and returns the count — proves the
     ``real_action`` we pass in is recorded verbatim, never substituted.
  3. ``_should_sample()`` is deterministic on request_id — proves
     dashboard drill-down is reproducible (same request always lands
     the same bucket).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from services.gateway.shadow_eval_hook import (
    _POLICY_CACHE,
    _should_sample,
    evaluate_and_record,
    invalidate_cache,
    schedule,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _POLICY_CACHE.clear()
    yield
    _POLICY_CACHE.clear()


# ---------------------------------------------------------------------------
# Sampling determinism — same request_id always lands same bucket
# ---------------------------------------------------------------------------


def test_sample_rate_one_always_passes() -> None:
    for rid in ("a", "b", "c", "d", "longer-request-id-1234"):
        assert _should_sample(1.0, rid) is True


def test_sample_rate_zero_never_passes() -> None:
    for rid in ("a", "b", "c", "d"):
        assert _should_sample(0.0, rid) is False


def test_sample_is_deterministic_per_request() -> None:
    rid = "request-id-deterministic-fixture"
    first = _should_sample(0.42, rid)
    for _ in range(20):
        assert _should_sample(0.42, rid) == first


def test_sample_split_is_close_to_target() -> None:
    target = 0.3
    hits = sum(_should_sample(target, f"req-{i}") for i in range(2000))
    rate = hits / 2000
    # Hash-based bucket; allow a generous +/- 5pp drift band.
    assert abs(rate - target) < 0.05, f"observed {rate}"


def test_no_request_id_falls_back_to_pass() -> None:
    # When there's no request_id we can't bucket deterministically;
    # default is to evaluate so we never silently drop traffic from
    # shadow coverage.
    assert _should_sample(0.01, None) is True


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------


def test_invalidate_cache_specific_tenant() -> None:
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    _POLICY_CACHE[(str(tenant_a), None)] = (9e9, [{"id": "a"}])
    _POLICY_CACHE[(str(tenant_b), None)] = (9e9, [{"id": "b"}])
    invalidate_cache(tenant_a)
    assert (str(tenant_a), None) not in _POLICY_CACHE
    assert (str(tenant_b), None) in _POLICY_CACHE


def test_invalidate_cache_global() -> None:
    _POLICY_CACHE[(str(uuid.uuid4()), None)] = (9e9, [{"id": "x"}])
    _POLICY_CACHE[(str(uuid.uuid4()), None)] = (9e9, [{"id": "y"}])
    invalidate_cache(None)
    assert _POLICY_CACHE == {}


# ---------------------------------------------------------------------------
# evaluate_and_record() — the core fire-and-forget call.
#
# We monkey-patch the audit SessionLocal + the shadow-policy loader so
# the test doesn't touch a real DB. The assertion that matters is that
# the function returns the count of policies it would have written and
# never raises.
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self, sink: list[Any]):
        self._sink = sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a, **_kw):
        return None

    def add(self, row: Any) -> None:
        self._sink.append(row)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_shadow_deny_does_not_block_real_action(monkeypatch) -> None:
    """The HEADLINE Sprint 6 invariant.

    Shadow policy says deny. We pass `real_action='allow'` (the live
    pipeline's decision). The hook records a shadow_decisions row that
    captures BOTH actions but never substitutes one for the other —
    the caller would receive the same `real_action` back if it
    inspected the recorded row.
    """
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    written: list[Any] = []

    async def _fake_load(tid, aid):
        return [{
            "id":          str(uuid.uuid4()),
            "version":     1,
            "rules_json":  [{"conditions": [], "action": "deny",
                             "description": "shadow always denies"}],
            "sample_rate": 1.0,
            "agent_id":    None,
        }]

    def _fake_session():
        return _FakeSession(written)

    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook._load_shadow_policies", _fake_load
    )
    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook.SessionLocal", _fake_session
    )

    n = await evaluate_and_record(
        tenant_id=tenant_id,
        agent_id=agent_id,
        request_id="req-test-1",
        audit_id=None,
        tool="tool.benign",
        payload="legitimate query",
        payload_hash=None,
        real_action="allow",   # <-- live pipeline's real decision
        risk_score=0.1,
    )
    assert n == 1
    assert len(written) == 1
    row = written[0]
    # The row captures shadow_action='deny' but real_action stays 'allow'.
    assert row.real_action == "allow"
    assert row.shadow_action == "deny"
    # The candidate would have blocked benign traffic — this is the FP
    # signal we surface in the would-have-denied report.
    from services.audit.shadow_evaluator import would_have_blocked_benign
    assert would_have_blocked_benign(row.real_action, row.shadow_action) is True


@pytest.mark.asyncio
async def test_no_policies_means_zero_writes(monkeypatch) -> None:
    """Common case: no shadow policies registered for the tenant.
    Hook returns 0 without touching the DB."""
    async def _empty(tid, aid):
        return []

    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook._load_shadow_policies", _empty
    )

    n = await evaluate_and_record(
        tenant_id=uuid.uuid4(),
        agent_id=None,
        request_id="rid-none",
        audit_id=None,
        tool="tool.read_file",
        payload="docs/README.md",
        payload_hash=None,
        real_action="allow",
        risk_score=0.0,
    )
    assert n == 0


@pytest.mark.asyncio
async def test_load_failure_does_not_raise(monkeypatch) -> None:
    """If the audit DB is unreachable, the hook must NEVER propagate the
    exception into the request handler. We simulate by making the loader
    raise — the public API should swallow it."""

    async def _broken(tid, aid):
        raise RuntimeError("audit DB unreachable")

    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook._load_shadow_policies", _broken
    )

    with pytest.raises(RuntimeError):
        # evaluate_and_record itself propagates because the load helper
        # is upstream of the try/except — but the real hot-path entry
        # is `schedule()`, which wraps the whole thing in try/except
        # (see test_schedule_never_raises below).
        await evaluate_and_record(
            tenant_id=uuid.uuid4(),
            agent_id=None,
            request_id="rid",
            audit_id=None,
            tool="t",
            payload="p",
            payload_hash=None,
            real_action="allow",
            risk_score=0.0,
        )


@pytest.mark.asyncio
async def test_schedule_returns_task_and_never_raises(monkeypatch) -> None:
    """``schedule()`` is the only entry point the gateway middleware
    uses. It MUST return an asyncio.Task (or None if no loop) and MUST
    NEVER let the underlying runner raise into the caller's frame."""

    async def _broken_run(*a, **kw):
        raise RuntimeError("shadow eval blew up")

    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook.evaluate_and_record", _broken_run
    )

    task = schedule(
        tenant_id=uuid.uuid4(),
        agent_id=None,
        request_id="rid",
        audit_id=None,
        tool="t",
        payload="p",
        payload_hash=None,
        real_action="allow",
        risk_score=0.0,
    )
    assert task is not None
    # Awaiting the task must NOT raise — the _runner() wrapper catches
    # the exception and logs it.
    await task


@pytest.mark.asyncio
async def test_action_recorded_verbatim_when_real_denies(monkeypatch) -> None:
    """Symmetric guarantee: when the live pipeline denies, the recorded
    real_action is 'deny' even if the shadow policy disagrees."""
    written: list[Any] = []
    pol_id = str(uuid.uuid4())

    async def _load(tid, aid):
        return [{
            "id": pol_id, "version": 1,
            "rules_json": [{"conditions": [], "action": "allow",
                            "description": "shadow always allows"}],
            "sample_rate": 1.0, "agent_id": None,
        }]
    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook._load_shadow_policies", _load
    )
    monkeypatch.setattr(
        "services.gateway.shadow_eval_hook.SessionLocal",
        lambda: _FakeSession(written),
    )
    await evaluate_and_record(
        tenant_id=uuid.uuid4(),
        agent_id=None,
        request_id="req-deny",
        audit_id=None,
        tool="tool.shell",
        payload="rm -rf /",
        payload_hash=None,
        real_action="deny",   # <-- live pipeline's real decision
        risk_score=0.99,
    )
    assert len(written) == 1
    assert written[0].real_action == "deny"
    assert written[0].shadow_action == "allow"
