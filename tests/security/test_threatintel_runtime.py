"""Sprint 7 — Threat-Intel runtime match tests."""
from __future__ import annotations

import pytest

from services.security.threatintel import runtime as ti_rt
from services.security.threatintel import store as ti_store
from services.security.threatintel.ioc import (
    KIND_DESTRUCTIVE_SHELL,
    KIND_EXFIL_HOST,
    SEV_HIGH,
    SOURCE_OPERATOR,
)


# Phase-2 cleanup 2026-06-15 — fake moved to tests/security/_fakes.py.
from tests.security._fakes import FakeRedis as _FakeRedis


class _BrokenRedis:
    """SMEMBERS always raises — used to verify fail-open."""
    async def smembers(self, k):
        raise RuntimeError("redis down")


@pytest.mark.asyncio
async def test_match_empty_cache_returns_false():
    r = _FakeRedis()
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, candidate="transfer.sh",
    ) is False


@pytest.mark.asyncio
async def test_match_substring_hits_when_iocs_seeded():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="transfer.sh",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST,
        candidate="https://transfer.sh/file.tgz",
    ) is True


@pytest.mark.asyncio
async def test_match_substring_case_insensitive():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="pastebin.com",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST,
        candidate="HTTPS://PASTEBIN.COM/foo",
    ) is True


@pytest.mark.asyncio
async def test_match_global_overlay_visible_to_every_tenant():
    """An IOC written to the GLOBAL overlay reaches every tenant's match."""
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id=ti_store.GLOBAL_TENANT_ID, kind=KIND_EXFIL_HOST,
        value="anonfiles.com", severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    for tid in ("tenant-A", "tenant-B", "tenant-C"):
        assert await ti_rt.match(
            r, tenant_id=tid, kind=KIND_EXFIL_HOST,
            candidate="https://anonfiles.com/x",
        ) is True


@pytest.mark.asyncio
async def test_match_any_or_short_circuits():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="x.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert await ti_rt.match_any(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST,
        candidates=["nope.com", "https://x.io/foo", "other.com"],
    ) is True
    assert await ti_rt.match_any(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST,
        candidates=["nope.com", "other.com"],
    ) is False


@pytest.mark.asyncio
async def test_match_destructive_shell_uses_regex():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_DESTRUCTIVE_SHELL,
        value=r"rm\s+-rf\s+/", severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_DESTRUCTIVE_SHELL,
        candidate="cd /tmp && rm -rf /  # oops",
    ) is True
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_DESTRUCTIVE_SHELL,
        candidate="ls -al",
    ) is False


@pytest.mark.asyncio
async def test_match_redis_fault_returns_false_fail_open():
    """Runtime fault must not raise into the request path."""
    r = _BrokenRedis()
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, candidate="transfer.sh",
    ) is False
    assert await ti_rt.match_any(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, candidates=["x", "y"],
    ) is False


@pytest.mark.asyncio
async def test_match_empty_candidate_returns_false():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="transfer.sh",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert await ti_rt.match(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, candidate="",
    ) is False
    assert await ti_rt.match_any(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, candidates=["", None],
    ) is False


@pytest.mark.asyncio
async def test_matches_for_kind_unions_tenant_and_global():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="tenant.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    await ti_store.upsert_ioc(
        r, tenant_id=ti_store.GLOBAL_TENANT_ID, kind=KIND_EXFIL_HOST,
        value="global.io", severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    vals = await ti_rt.matches_for_kind(r, tenant_id="t1", kind=KIND_EXFIL_HOST)
    assert vals == {"tenant.io", "global.io"}
