"""Sprint 5 — IAG store integration tests.

Uses a minimal in-memory async Redis fake (same shape as test_incident_recorder
but extended with SADD / SMEMBERS / DELETE for the SET-based IAG layout).
Verifies the writer-then-reader round-trip + the load_graph() walker.
"""
from __future__ import annotations

import pytest

from services.security.iag import store
from services.security.iag.graph import (
    KIND_TABLE,
    ResourceMeta,
    SENS_HIGH,
    SENS_MEDIUM,
)


# Phase-2 cleanup 2026-06-15 — fake moved to tests/security/_fakes.py.
from tests.security._fakes import FakeRedis as _FakeRedis


@pytest.mark.asyncio
async def test_iag_store_round_trip_agent_roles():
    r = _FakeRedis()
    await store.upsert_agent_roles(r, "t1", "agA", {"r_dba", "r_readonly"})
    out = await store.get_agent_roles(r, "t1", "agA")
    assert out == {"r_dba", "r_readonly"}


@pytest.mark.asyncio
async def test_iag_store_replace_semantics_not_accumulate():
    """A second upsert must REPLACE the role set, not add to it.

    An agent losing a role on the next ingestion pass should remove the
    old edge — otherwise revoked permissions linger forever in the IAG.
    """
    r = _FakeRedis()
    await store.upsert_agent_roles(r, "t1", "agA", {"r1", "r2"})
    await store.upsert_agent_roles(r, "t1", "agA", {"r2", "r3"})
    out = await store.get_agent_roles(r, "t1", "agA")
    assert out == {"r2", "r3"}


@pytest.mark.asyncio
async def test_iag_store_resource_meta_round_trip():
    r = _FakeRedis()
    meta = ResourceMeta(
        resource_id="customers",
        kind=KIND_TABLE,
        label="PII customer rows",
        sensitivity=SENS_HIGH,
    )
    await store.upsert_resource_meta(r, "t1", meta)
    back = await store.get_resource_meta(r, "t1", "customers")
    assert back == meta


@pytest.mark.asyncio
async def test_iag_store_load_graph_walks_full_chain():
    r = _FakeRedis()
    # agA -> r_dba -> p_select_customers -> customers
    # agA -> r_dba -> p_select_orders    -> orders
    await store.upsert_agent_roles(r, "t1", "agA", {"r_dba"})
    await store.upsert_role_perms(r, "t1", "r_dba", {"p_sel_cust", "p_sel_ord"})
    await store.upsert_perm_resources(r, "t1", "p_sel_cust", {"customers"})
    await store.upsert_perm_resources(r, "t1", "p_sel_ord", {"orders"})
    await store.upsert_resource_meta(r, "t1", ResourceMeta(
        resource_id="customers", kind=KIND_TABLE,
        label="customers", sensitivity=SENS_HIGH,
    ))
    await store.upsert_resource_meta(r, "t1", ResourceMeta(
        resource_id="orders", kind=KIND_TABLE,
        label="orders", sensitivity=SENS_MEDIUM,
    ))

    agent_roles, role_perms, perm_resources, resource_meta = await store.load_graph(
        r, "t1", "agA",
    )
    assert agent_roles == {"r_dba"}
    assert role_perms == {"r_dba": {"p_sel_cust", "p_sel_ord"}}
    assert perm_resources == {"p_sel_cust": {"customers"}, "p_sel_ord": {"orders"}}
    assert set(resource_meta.keys()) == {"customers", "orders"}
    assert resource_meta["customers"].sensitivity == SENS_HIGH


@pytest.mark.asyncio
async def test_iag_store_unknown_agent_returns_empty_graph():
    r = _FakeRedis()
    agent_roles, role_perms, perm_resources, resource_meta = await store.load_graph(
        r, "t1", "ghost-agent",
    )
    assert agent_roles == set()
    assert role_perms == {}
    assert perm_resources == {}
    assert resource_meta == {}


@pytest.mark.asyncio
async def test_iag_store_last_ingest_ts_stamps_and_reads():
    r = _FakeRedis()
    assert await store.get_last_ingest_ts(r, "t1") == 0.0
    await store.stamp_ingestion_done(r, "t1", now_ts=1781500000.0)
    assert await store.get_last_ingest_ts(r, "t1") == 1781500000.0
