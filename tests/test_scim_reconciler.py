"""Test the SCIM reconciler async path with a stubbed ScimClient."""
from __future__ import annotations

import pytest

from services.policy.scim_agent import AgentRecord


@pytest.mark.asyncio
async def test_disabled_when_no_config(monkeypatch):
    monkeypatch.setenv("SCIM_BASE_URL", "")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "")
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.identity import scim_reconciler as _sr
    _reload(_sr)

    r = await _sr.run_once_async([
        AgentRecord("ag_1", "scim://acme/Users/u_1", "VERIFIED"),
    ])
    assert r == []
    s = _sr.summarize(r)
    assert s["enabled"] is False


@pytest.mark.asyncio
async def test_reconciles_active_suspended_notfound(monkeypatch):
    monkeypatch.setenv("SCIM_BASE_URL", "https://scim.example/scim/v2")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "test-token")
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.identity import scim_reconciler as _sr
    _reload(_sr)

    # Stub the ScimClient constructor to return a client whose
    # lookup_user returns fixed values per ref.
    directory = {
        "u_alice":     "ACTIVE",
        "u_bob":       "SUSPENDED",
        "u_missing":   "NOT_FOUND",
    }

    class _StubClient:
        def __init__(self, *_a, **_kw):
            pass
        async def lookup_user(self, ref: str):
            uid = ref.rsplit("/", 1)[-1]
            return directory.get(uid, "NOT_FOUND")

    monkeypatch.setattr(_sr, "ScimClient", _StubClient)

    agents = [
        AgentRecord("ag_1", "scim://acme/Users/u_alice",   "VERIFIED"),
        AgentRecord("ag_2", "scim://acme/Users/u_bob",     "VERIFIED"),
        AgentRecord("ag_3", "scim://acme/Users/u_missing", "VERIFIED"),
    ]
    results = await _sr.run_once_async(agents)
    by_id = {r.agent_id: r for r in results}
    assert by_id["ag_1"].action == "OK"
    assert by_id["ag_2"].action == "QUARANTINE"
    assert by_id["ag_3"].action == "QUARANTINE"

    s = _sr.summarize(results)
    assert s["enabled"] is True
    assert s["totals"]["QUARANTINE"] == 2
    assert s["totals"]["OK"] == 1
    assert len(s["quarantine"]) == 2


@pytest.mark.asyncio
async def test_transient_scim_does_not_mass_quarantine(monkeypatch):
    """SCIM outage → all agents stay in current state, none quarantined."""
    monkeypatch.setenv("SCIM_BASE_URL", "https://scim.example/scim/v2")
    monkeypatch.setenv("SCIM_BEARER_TOKEN", "test-token")
    from importlib import reload as _reload

    from sdk.common import config as _cfg
    _reload(_cfg)
    from services.identity import scim_reconciler as _sr
    _reload(_sr)

    class _FailingClient:
        def __init__(self, *_a, **_kw):
            pass
        async def lookup_user(self, ref: str):
            from sdk.common.scim_client import ScimTransientError
            raise ScimTransientError("scim_5xx: 503")

    monkeypatch.setattr(_sr, "ScimClient", _FailingClient)

    agents = [AgentRecord("ag_1", "scim://acme/Users/u_1", "VERIFIED")]
    results = await _sr.run_once_async(agents)
    # Reconcile treats transient errors as OK (see policy/scim_agent.py).
    by_id = {r.agent_id: r for r in results}
    assert by_id["ag_1"].action == "OK"
    assert "transient" in by_id["ag_1"].reason.lower() or "prefetch" in by_id["ag_1"].reason.lower()
