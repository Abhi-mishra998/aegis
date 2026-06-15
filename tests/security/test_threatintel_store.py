"""Sprint 7 — Threat-Intel store round-trip tests."""
from __future__ import annotations

import pytest

from services.security.threatintel import store as ti_store
from services.security.threatintel.ioc import (
    KIND_DESTRUCTIVE_SHELL,
    KIND_EXFIL_HOST,
    SEV_HIGH,
    SOURCE_OPERATOR,
)


# Phase-2 cleanup 2026-06-15 — fake moved to tests/security/_fakes.py.
from tests.security._fakes import FakeRedis as _FakeRedis


@pytest.mark.asyncio
async def test_upsert_then_list_returns_record():
    r = _FakeRedis()
    rec = await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="MY-BAD-HOST.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR, actor="alice",
    )
    # Lowercased on write for substring kinds.
    assert rec.value == "my-bad-host.io"
    items = await ti_store.list_iocs(r, tenant_id="t1", kind=KIND_EXFIL_HOST)
    assert len(items) == 1
    assert items[0].id == rec.id
    assert items[0].actor == "alice"


@pytest.mark.asyncio
async def test_upsert_is_idempotent_on_same_value():
    r = _FakeRedis()
    rec1 = await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="x.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    rec2 = await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="x.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert rec1.id == rec2.id
    items = await ti_store.list_iocs(r, tenant_id="t1")
    assert len(items) == 1


@pytest.mark.asyncio
async def test_destructive_shell_keeps_case_for_regex():
    r = _FakeRedis()
    rec = await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_DESTRUCTIVE_SHELL,
        value=r"rm\s+-rf\s+/", severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    assert rec.value == r"rm\s+-rf\s+/"


@pytest.mark.asyncio
async def test_delete_removes_from_values_index_and_meta():
    r = _FakeRedis()
    rec = await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="x.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    deleted = await ti_store.delete_ioc(r, tenant_id="t1", ioc_id=rec.id)
    assert deleted is True
    items = await ti_store.list_iocs(r, tenant_id="t1")
    assert items == []
    vals = await ti_store.values_for_kind(r, tenant_id="t1", kind=KIND_EXFIL_HOST)
    assert vals == set()


@pytest.mark.asyncio
async def test_delete_unknown_id_returns_false():
    r = _FakeRedis()
    assert await ti_store.delete_ioc(r, tenant_id="t1", ioc_id="nope") is False


@pytest.mark.asyncio
async def test_list_filters_by_kind_and_source():
    r = _FakeRedis()
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_EXFIL_HOST, value="a.io",
        severity=SEV_HIGH, source=SOURCE_OPERATOR,
    )
    await ti_store.upsert_ioc(
        r, tenant_id="t1", kind=KIND_DESTRUCTIVE_SHELL, value="rm",
        severity=SEV_HIGH, source="feed",
    )
    only_exfil = await ti_store.list_iocs(r, tenant_id="t1", kind=KIND_EXFIL_HOST)
    assert len(only_exfil) == 1 and only_exfil[0].kind == KIND_EXFIL_HOST
    only_feed = await ti_store.list_iocs(r, tenant_id="t1", source="feed")
    assert len(only_feed) == 1 and only_feed[0].source == "feed"


@pytest.mark.asyncio
async def test_feed_upsert_round_trip():
    r = _FakeRedis()
    await ti_store.upsert_feed(
        r, tenant_id="t1", name="my-feed",
        url="https://example.com/iocs.txt", format="text",
        refresh_seconds=3600,
    )
    feeds = await ti_store.list_feeds(r, tenant_id="t1")
    assert "my-feed" in feeds
    assert feeds["my-feed"]["url"] == "https://example.com/iocs.txt"
    assert feeds["my-feed"]["refresh_seconds"] == 3600


@pytest.mark.asyncio
async def test_stamp_refresh_and_get_round_trip():
    r = _FakeRedis()
    assert await ti_store.get_last_refresh(r, tenant_id="t1") == 0.0
    await ti_store.stamp_refresh(r, tenant_id="t1", now_ts=1781500000.0)
    assert await ti_store.get_last_refresh(r, tenant_id="t1") == 1781500000.0
