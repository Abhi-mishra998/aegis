"""Sprint 6 — Remediation executor integration tests.

Uses a minimal in-memory async Redis fake covering the surface the
executor actually uses (LRANGE/RPUSH/SADD/SISMEMBER/SREM/EXPIRE/PUBLISH/
XADD/HGETALL/HSET).
"""
from __future__ import annotations

import json

import pytest

from services.security.remediation import executor
from services.security.remediation.actions import (
    KIND_AUDIT_LOG,
    KIND_KILL_ACTIVE_TOKENS,
    KIND_PAGE_ONCALL,
    KIND_REVOKE_API_KEY,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
)
from services.security.remediation.policy import RemediationPolicy


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.streams: dict[str, list[dict[str, str]]] = {}
        self.publishes: list[tuple[str, str]] = []

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

    async def expire(self, k, ex):
        return True

    async def sadd(self, k, *vals):
        s = self.sets.setdefault(k, set())
        before = len(s)
        s.update(str(v) for v in vals)
        return len(s) - before

    async def srem(self, k, *vals):
        s = self.sets.get(k, set())
        before = len(s)
        for v in vals:
            s.discard(str(v))
        return before - len(s)

    async def sismember(self, k, v):
        return str(v) in self.sets.get(k, set())

    async def hgetall(self, k):
        h = self.hashes.get(k, {})
        return {kk.encode(): vv.encode() for kk, vv in h.items()}

    async def hset(self, k, field=None, value=None, mapping=None, **kw):
        h = self.hashes.setdefault(k, {})
        if field is not None:
            h[field] = str(value) if value is not None else ""
        if mapping:
            for kk, vv in mapping.items():
                h[kk] = str(vv) if vv is not None else ""
        for kk, vv in kw.items():
            h[kk] = str(vv) if vv is not None else ""
        return 1

    async def publish(self, channel, payload):
        self.publishes.append((channel, payload))
        return 1

    async def xadd(self, stream, fields):
        self.streams.setdefault(stream, []).append(dict(fields))
        return f"{len(self.streams[stream])}-0"


@pytest.mark.asyncio
async def test_executor_fires_all_default_actions_except_paging():
    r = _FakeRedis()
    actions = await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    kinds_status = {a.kind: a.status for a in actions}
    assert kinds_status[KIND_REVOKE_API_KEY]    == STATUS_DONE
    assert kinds_status[KIND_KILL_ACTIVE_TOKENS] == STATUS_DONE
    # Default policy has page_oncall=False so this is SKIPPED.
    assert kinds_status[KIND_PAGE_ONCALL]       == STATUS_SKIPPED
    assert kinds_status[KIND_AUDIT_LOG]         == STATUS_DONE
    # Side effects verified.
    assert "agA" in r.sets["acp:remediation:revoked_agents:t1"]
    assert any("acp:token:revocations" == ch for ch, _ in r.publishes)
    assert "acp:audit:writes" in r.streams


@pytest.mark.asyncio
async def test_executor_publishes_revocation_payload_correctly():
    r = _FakeRedis()
    await executor.execute(r, incident_id="INC-X", tenant_id="t1", agent_id="agA")
    pub_payloads = [json.loads(payload) for ch, payload in r.publishes
                    if ch == "acp:token:revocations"]
    assert len(pub_payloads) == 1
    payload = pub_payloads[0]
    assert payload["tenant_id"]     == "t1"
    assert payload["agent_id"]      == "agA"
    assert payload["all_for_agent"] is True
    assert payload["reason"]        == "auto_remediation"


@pytest.mark.asyncio
async def test_executor_idempotent_second_call_skips_done_actions():
    r = _FakeRedis()
    first = await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    assert all(a.kind == KIND_PAGE_ONCALL or a.status != STATUS_SKIPPED for a in first)
    # Second invocation — every previously-DONE action becomes SKIPPED.
    second = await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    statuses = {a.kind: a.status for a in second}
    assert statuses[KIND_REVOKE_API_KEY]     == STATUS_SKIPPED
    assert statuses[KIND_KILL_ACTIVE_TOKENS] == STATUS_SKIPPED
    assert statuses[KIND_AUDIT_LOG]          == STATUS_SKIPPED
    # No fresh publish.
    assert len(r.publishes) == 1


@pytest.mark.asyncio
async def test_executor_replay_force_reruns_actions():
    r = _FakeRedis()
    await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    rep = await executor.replay(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    statuses = {a.kind: a.status for a in rep}
    # All enabled actions re-fire on replay.
    assert statuses[KIND_REVOKE_API_KEY]     == STATUS_DONE
    assert statuses[KIND_KILL_ACTIVE_TOKENS] == STATUS_DONE
    assert statuses[KIND_AUDIT_LOG]          == STATUS_DONE
    # Ledger appended, not rewritten.
    full_ledger = await executor.get_ledger(r, "t1", "INC-1")
    assert len(full_ledger) >= 8   # 4 from first pass + 4 from replay


@pytest.mark.asyncio
async def test_executor_dry_run_does_not_mutate_redis():
    r = _FakeRedis()
    p = RemediationPolicy(
        revoke_api_keys=True,
        kill_active_tokens=True,
        page_oncall=True,
        webhook_url="https://example.com/hook",
        audit_log=True,
    )
    actions = await executor.execute(
        r, incident_id="INC-DR", tenant_id="t1", agent_id="agA",
        policy=p, dry_run=True,
    )
    assert len(actions) == 4
    assert all(a.result in ("dry_run", "policy disabled") for a in actions)
    # Zero mutations.
    assert r.sets == {}
    assert r.publishes == []
    assert r.streams == {}
    assert r.lists == {}


@pytest.mark.asyncio
async def test_executor_records_per_action_status_in_ledger():
    r = _FakeRedis()
    await executor.execute(
        r, incident_id="INC-L", tenant_id="t1", agent_id="agA",
    )
    ledger = await executor.get_ledger(r, "t1", "INC-L")
    assert len(ledger) == 4
    for action in ledger:
        assert action.incident_id == "INC-L"
        assert action.tenant_id   == "t1"
        assert action.agent_id    == "agA"
        assert action.status in (STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED)


@pytest.mark.asyncio
async def test_executor_policy_disabled_marks_skipped_with_reason():
    r = _FakeRedis()
    p = RemediationPolicy(
        revoke_api_keys=False, kill_active_tokens=False,
        page_oncall=False, audit_log=False,
    )
    actions = await executor.execute(
        r, incident_id="INC-OFF", tenant_id="t1", agent_id="agA", policy=p,
    )
    assert all(a.status == STATUS_SKIPPED for a in actions)
    assert all(a.result == "policy disabled" for a in actions)
    assert r.sets == {}
    assert r.publishes == []
    assert r.streams == {}


@pytest.mark.asyncio
async def test_is_agent_revoked_round_trip():
    r = _FakeRedis()
    assert await executor.is_agent_revoked(r, "t1", "agA") is False
    await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    assert await executor.is_agent_revoked(r, "t1", "agA") is True


@pytest.mark.asyncio
async def test_release_revoked_agent_clears_set():
    r = _FakeRedis()
    await executor.execute(
        r, incident_id="INC-1", tenant_id="t1", agent_id="agA",
    )
    removed = await executor.release_revoked_agent(r, "t1", "agA")
    assert removed is True
    assert await executor.is_agent_revoked(r, "t1", "agA") is False
