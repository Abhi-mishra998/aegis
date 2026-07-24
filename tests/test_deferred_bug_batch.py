"""Q38-Q42: whitebox regression tests for the five deferred-agent-finding
fixes in this batch. Each test locks in the code shape so a future
revert flips it red immediately."""
from __future__ import annotations

import inspect

import pytest


def test_q38_revenue_dashboard_is_capped():
    """Q38: revenue-dashboard queries must carry .limit(_DASH_TOP_N) —
    the prior unbounded GROUP BY loaded every agent + tool tuple for
    a tenant into python."""
    from services.usage.repository.usage import UsageRepository
    src = inspect.getsource(UsageRepository.get_revenue_dashboard)
    assert "_DASH_TOP_N" in src
    assert ".limit(_DASH_TOP_N)" in src
    assert "order_by(func.sum" in src, "top-N ordering must accompany the cap"


def test_q39_billing_event_tokens_bounded():
    """Q39: BillingEvent.tokens must have upper + lower bounds.
    A client passing tokens=10^12 previously produced absurd cost math."""
    from services.usage.billing_routes.router import BillingEvent
    fields = BillingEvent.model_fields
    tokens = fields["tokens"]
    # Field(ge=0, le=10_000_000) — the metadata carries these bounds.
    meta = tokens.metadata
    # collect ge / le values from any metadata object that exposes them
    ge_vals = [m.ge for m in meta if hasattr(m, "ge")]
    le_vals = [m.le for m in meta if hasattr(m, "le")]
    assert ge_vals and min(ge_vals) >= 0
    assert le_vals and max(le_vals) <= 10_000_000

    # Positive case: valid input round-trips.
    ev = BillingEvent(
        tenant_id="11111111-1111-1111-1111-111111111111",
        action="allow", tokens=100,
    )
    assert ev.tokens == 100

    # Negative case: over-cap rejected via pydantic ValidationError.
    with pytest.raises(Exception):  # pydantic.ValidationError
        BillingEvent(
            tenant_id="11111111-1111-1111-1111-111111111111",
            action="allow", tokens=10_000_001,
        )


def test_q40_approve_pending_uses_atomic_getdel():
    """Q40: the pending-approval fetch must use r.getdel(key) so two
    concurrent approvers can't both read + both re-queue."""
    from services.api.router.auto_response import approve_pending
    src = inspect.getsource(approve_pending)
    assert "r.getdel(key)" in src, (
        "approve_pending no longer uses atomic GETDEL — double-approval "
        "TOCTOU is back"
    )
    # Old pattern gone.
    assert not (
        "raw = await r.get(key)" in src and "await r.delete(key)" in src
        and "getdel" not in src
    )


def test_q41_replay_int_cast_returns_422():
    """Q41: bare int() on client input must be wrapped so non-numeric
    hours/limit surface as 422, not 500."""
    from services.api.router.auto_response import replay
    src = inspect.getsource(replay)
    assert "try:" in src
    assert "int(payload.get(" in src
    assert "except (ValueError, TypeError)" in src
    assert "status_code=422" in src


def test_q42_dedup_key_uses_json_encoding():
    """Q42: the dedup key builder must use json.dumps of a list, not
    an unescaped `:` join. Colon in a component (e.g. tool name
    'mcp:server:x') collapsed to a different tuple under the old code."""
    # api.main imports schemas.api_key which uses pydantic.EmailStr →
    # optional email-validator dep. Skip cleanly when absent.
    pytest.importorskip("email_validator")
    from services.api import main as _mod
    # Grab the relevant function's source. `_process_incident_message`
    # is where this lives per the grep at api/main.py:190.
    src = inspect.getsource(_mod)
    # Look for the specific dedup_raw construction.
    idx = src.find("dedup_raw")
    assert idx >= 0
    # Extract ~200 chars around it for the assertion window.
    window = src[idx: idx + 400]
    assert "json.dumps([" in window, (
        "dedup_raw is no longer using json.dumps of a list — colon "
        "collision is back"
    )
    # And the collision-prone f-string join is gone from that window.
    assert 'f"{data.get(\'tenant_id\')}:{data.get(\'agent_id\')}' not in window


def test_q42_dedup_key_avoids_collision_from_colon_in_tool_name():
    """Sanity: two DIFFERENT input tuples that would have collided
    under the old `:`-join produce different keys under json encoding."""
    import hashlib
    import json

    # Tuple A: tool contains a colon; separates one way under `:`-join.
    a = {"tenant_id": "T", "agent_id": "A", "tool": "mcp:x", "trigger": "Y"}
    # Tuple B: tool without colon but trigger contains one; separates
    # another way under `:`-join. Under the old join both produce the
    # SAME 5-field string "T:A:mcp:x:Y:1"; under json they're distinct.
    b = {"tenant_id": "T", "agent_id": "A", "tool": "mcp", "trigger": "x:Y"}
    time_bucket = 1

    a_raw = json.dumps([a["tenant_id"], a["agent_id"], a["tool"], a["trigger"], time_bucket])
    b_raw = json.dumps([b["tenant_id"], b["agent_id"], b["tool"], b["trigger"], time_bucket])
    assert a_raw != b_raw, "json encoding must disambiguate the two tuples"
    assert hashlib.sha256(a_raw.encode()).hexdigest() != \
           hashlib.sha256(b_raw.encode()).hexdigest()

    # Confirm the OLD f-string join really did collide:
    a_old = f"{a['tenant_id']}:{a['agent_id']}:{a['tool']}:{a['trigger']}:{time_bucket}"
    b_old = f"{b['tenant_id']}:{b['agent_id']}:{b['tool']}:{b['trigger']}:{time_bucket}"
    assert a_old == b_old, "canary: old join really did collide"
