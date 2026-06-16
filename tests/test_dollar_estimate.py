"""
Sprint 8 — Unit tests for the Blast-Radius dollar formula.

Exercises the gateway's `_dollar_estimate` helper directly. The helper
reaches into `service_client.get_tenant_metadata`; we patch that to
return a synthetic dict so the test surface is the pure formula.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.gateway.routers import iag


def _run(by_kind, system_values, *, raise_meta=False):
    async def _go():
        if raise_meta:
            with patch.object(
                iag.service_client,
                "get_tenant_metadata",
                AsyncMock(side_effect=RuntimeError("boom")),
            ):
                return await iag._dollar_estimate(
                    "11111111-1111-1111-1111-111111111111", by_kind,
                )
        with patch.object(
            iag.service_client,
            "get_tenant_metadata",
            AsyncMock(return_value={"system_values": system_values}),
        ):
            return await iag._dollar_estimate(
                "11111111-1111-1111-1111-111111111111", by_kind,
            )

    return asyncio.new_event_loop().run_until_complete(_go())


def test_zero_total_when_no_weights():
    total, by_kind_dollars, sv = _run({"table": 3, "api": 2}, {})
    assert total == 0
    assert by_kind_dollars == {}
    assert sv == {}


def test_basic_multiplication():
    total, by_kind_dollars, sv = _run(
        {"table": 3, "api": 2},
        {"table": 50_000, "api": 100_000},
    )
    assert total == 3 * 50_000 + 2 * 100_000  # 350_000
    assert by_kind_dollars == {"table": 150_000, "api": 200_000}
    assert sv == {"table": 50_000, "api": 100_000}


def test_kinds_without_weights_skipped():
    total, by_kind_dollars, _sv = _run(
        {"table": 1, "secret": 4},  # only `table` has a weight
        {"table": 50_000},
    )
    assert total == 50_000
    assert by_kind_dollars == {"table": 50_000}


def test_negative_or_garbage_weights_ignored():
    total, by_kind_dollars, sv = _run(
        {"table": 1, "api": 1},
        {"table": -100, "api": "not-a-number"},
    )
    assert total == 0
    assert by_kind_dollars == {}
    assert sv == {}  # both filtered out by the int() + >0 guard


def test_meta_fetch_failure_collapses_to_zero():
    total, by_kind_dollars, sv = _run(
        {"table": 5}, {"table": 100}, raise_meta=True,
    )
    assert total == 0
    assert by_kind_dollars == {}
    assert sv == {}


def test_case_insensitive_kind_lookup():
    total, _bkd, _sv = _run(
        {"Table": 2, "API": 3},
        {"table": 10_000, "api": 20_000},
    )
    assert total == 2 * 10_000 + 3 * 20_000


def test_zero_count_yields_zero_dollar():
    total, by_kind_dollars, _sv = _run(
        {"table": 0, "api": 4},
        {"table": 50_000, "api": 100_000},
    )
    assert total == 4 * 100_000
    assert "table" not in by_kind_dollars
    assert by_kind_dollars["api"] == 400_000


def test_non_dict_system_values_collapses_to_zero():
    total, by_kind_dollars, sv = _run({"table": 5}, "garbage")
    assert total == 0
    assert by_kind_dollars == {}
    assert sv == {}
