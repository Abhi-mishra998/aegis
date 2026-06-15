"""
Sprint 4 — Recorder + store integration tests.

Uses a minimal in-memory async Redis fake so the test is hermetic.
Verifies grouping (session, cross-agent, agent fallback), idempotent open,
status transitions, and the get/list read API.
"""
from __future__ import annotations

import json

import pytest

from services.security.incidents import recorder, store
from services.security.incidents.storyline import STATUS_BLOCKED, STATUS_QUARANTINED


# ---------------------------------------------------------------------------
# Minimal async Redis fake — just the subset the recorder + store use.
# ---------------------------------------------------------------------------
class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, k, v, ex=None, nx=False):
        if nx and k in self.kv:
            return False
        self.kv[k] = str(v)
        if ex:
            self.ttls[k] = ex
        return True

    async def get(self, k):
        v = self.kv.get(k)
        return v.encode() if isinstance(v, str) else v

    async def expire(self, k, ex):
        self.ttls[k] = ex
        return True

    async def rpush(self, k, *vals):
        lst = self.lists.setdefault(k, [])
        for v in vals:
            lst.append(v if isinstance(v, str) else str(v))
        return len(lst)

    async def lrange(self, k, start, end):
        lst = self.lists.get(k, [])
        if end == -1:
            return [x.encode() for x in lst]
        return [x.encode() for x in lst[start: end + 1]]

    async def hset(self, k, field=None, value=None, mapping=None, **kw):
        # redis-py supports two call shapes the recorder relies on:
        #   hset(name, field, value)           — single-field write
        #   hset(name, mapping={field: value}) — batch write
        h = self.hashes.setdefault(k, {})
        wrote = 0
        if field is not None:
            h[field] = str(value) if value is not None else ""
            wrote += 1
        if mapping:
            for kk, vv in mapping.items():
                h[kk] = str(vv) if vv is not None else ""
                wrote += 1
        for kk, vv in kw.items():
            h[kk] = str(vv) if vv is not None else ""
            wrote += 1
        return wrote

    async def hsetnx(self, k, field, value):
        h = self.hashes.setdefault(k, {})
        if field in h:
            return 0
        h[field] = str(value)
        return 1

    async def hget(self, k, field):
        h = self.hashes.get(k, {})
        v = h.get(field)
        return v.encode() if isinstance(v, str) else v

    async def hgetall(self, k):
        h = self.hashes.get(k, {})
        return {kk.encode(): vv.encode() for kk, vv in h.items()}

    async def zadd(self, k, mapping):
        z = self.zsets.setdefault(k, {})
        for m, s in mapping.items():
            z[m] = float(s)
        return len(mapping)

    async def zrevrangebyscore(self, k, max_, min_, start=0, num=10):
        z = self.zsets.get(k, {})
        items = [(m, s) for m, s in z.items() if (min_ == "-inf" or s >= float(min_))
                 and (max_ == "+inf" or s <= float(max_))]
        items.sort(key=lambda x: x[1], reverse=True)
        out = items[start: start + num]
        return [m.encode() for m, _ in out]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _mitre(t="TA0010", tech="T1567.002 Exfiltration", obj="exfiltration"):
    return t, tech, obj


@pytest.mark.asyncio
async def test_recorder_opens_then_appends_within_same_session():
    r = _FakeRedis()
    tactic, tech, obj = _mitre("TA0007", "T1087 Account Discovery", "discovery")
    inc1 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess-1",
        signal_id="schema_recon", mitre_tactic=tactic, mitre_technique=tech,
        objective=obj, tier="monitor", policy_id="", target="information_schema",
        explanation="recon", risk_score=10, now_ts=100.0,
    )
    assert inc1.startswith("INC-")

    tactic, tech, obj = _mitre()
    inc2 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess-1",
        signal_id="external_pii_exfil", mitre_tactic=tactic,
        mitre_technique=tech, objective=obj, tier="deny",
        policy_id="SEC-EXFIL-001", target="transfer.sh",
        explanation="POST with PII to known exfil host", risk_score=95,
        now_ts=200.0,
    )
    # Same session = same incident.
    assert inc2 == inc1

    story = await store.get(r, "t1", inc1)
    assert story is not None
    assert story.status == STATUS_BLOCKED
    assert story.blocked_at_step == 2
    assert story.blocking_policy_id == "SEC-EXFIL-001"
    assert story.mitre_tactic_chain == ["TA0007", "TA0010"]
    assert story.participating_agents == ["agA"]
    assert len(story.steps) == 2


@pytest.mark.asyncio
async def test_recorder_distinct_sessions_get_distinct_incidents():
    r = _FakeRedis()
    inc_a = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess-A",
        signal_id="schema_recon", mitre_tactic="TA0007",
        mitre_technique="T1087", objective="discovery", tier="monitor",
        policy_id="", target="", explanation="", risk_score=10, now_ts=100.0,
    )
    inc_b = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess-B",
        signal_id="schema_recon", mitre_tactic="TA0007",
        mitre_technique="T1087", objective="discovery", tier="monitor",
        policy_id="", target="", explanation="", risk_score=10, now_ts=110.0,
    )
    assert inc_a != inc_b


@pytest.mark.asyncio
async def test_recorder_cross_agent_folds_into_one_story():
    r = _FakeRedis()
    chain = {"chain": "cross_agent_pii_exfil",
             "agent_ids": ["agA", "agB", "agC", "agD"]}
    inc1 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess-A",
        signal_id="bulk_pii_egress_above_threshold",
        mitre_tactic="TA0009", mitre_technique="T1213",
        objective="collection", tier="escalate", policy_id="HC-PII-001",
        target="customers", explanation="bulk PII read", risk_score=50,
        cross_agent_chain=chain, now_ts=100.0,
    )
    # Agent B doing the next step — different session — still folds in.
    inc2 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agB", session_id="sess-B-different",
        signal_id="compression_for_exfil",
        mitre_tactic="TA0009", mitre_technique="T1560",
        objective="collection", tier="monitor", policy_id="",
        target="/tmp/c.tgz", explanation="compression", risk_score=35,
        cross_agent_chain=chain, now_ts=200.0,
    )
    assert inc1 == inc2
    story = await store.get(r, "t1", inc1)
    assert sorted(story.participating_agents) == ["agA", "agB"]


@pytest.mark.asyncio
async def test_recorder_agent_fallback_when_no_session():
    """No session_id and no cross-agent chain — should still group by agent."""
    r = _FakeRedis()
    inc1 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="",
        signal_id="schema_recon", mitre_tactic="TA0007",
        mitre_technique="T1087", objective="discovery", tier="monitor",
        policy_id="", target="", explanation="", risk_score=10, now_ts=100.0,
    )
    inc2 = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="",
        signal_id="bulk_pii_egress_above_threshold", mitre_tactic="TA0009",
        mitre_technique="T1213", objective="collection", tier="escalate",
        policy_id="HC-PII-001", target="customers", explanation="bulk",
        risk_score=50, now_ts=200.0,
    )
    assert inc1 == inc2


@pytest.mark.asyncio
async def test_recorder_quarantine_status_wins():
    r = _FakeRedis()
    inc = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess",
        signal_id="schema_recon", mitre_tactic="TA0007",
        mitre_technique="T1087", objective="discovery", tier="monitor",
        policy_id="", target="", explanation="", risk_score=10, now_ts=100.0,
    )
    await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="sess",
        signal_id="attack_chain_match", mitre_tactic="TA0010",
        mitre_technique="T1020", objective="exfiltration", tier="quarantine",
        policy_id="SEC-CHAIN-DENY-001", target="", explanation="chain",
        risk_score=100, now_ts=200.0,
    )
    story = await store.get(r, "t1", inc)
    assert story.status == STATUS_QUARANTINED


@pytest.mark.asyncio
async def test_store_get_returns_none_for_unknown_incident():
    r = _FakeRedis()
    assert await store.get(r, "t1", "INC-DOES-NOT-EXIST") is None


@pytest.mark.asyncio
async def test_store_list_recent_returns_open_incidents():
    r = _FakeRedis()
    inc_old = await recorder.record_step(
        r, tenant_id="t1", agent_id="agA", session_id="s1",
        signal_id="schema_recon", mitre_tactic="TA0007",
        mitre_technique="T1087", objective="discovery", tier="monitor",
        policy_id="", target="", explanation="", risk_score=10, now_ts=100.0,
    )
    inc_new = await recorder.record_step(
        r, tenant_id="t1", agent_id="agB", session_id="s2",
        signal_id="external_pii_exfil", mitre_tactic="TA0010",
        mitre_technique="T1567.002", objective="exfiltration", tier="deny",
        policy_id="SEC-EXFIL-001", target="transfer.sh", explanation="exfil",
        risk_score=95, now_ts=200.0,
    )
    lst = await store.list_recent(r, "t1", since_ts=0.0)
    ids = [s.incident_id for s in lst]
    # Newest-first.
    assert ids[0] == inc_new
    assert inc_old in ids
