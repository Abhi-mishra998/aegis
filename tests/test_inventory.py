"""
Sprint 4 — Unit tests for the workspace inventory aggregator.

The endpoint itself talks to Postgres; we exercise it without a DB by
mocking the SQLAlchemy result. Verifies:

  - by_provider correctly buckets metadata.provider (incl. unknown).
  - by_risk + by_status + high_risk rollups.
  - wizard_provisioned counts only metadata.wizard=true rows.
  - Empty workspace returns zeros (not None).
"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _row(status, risk, metadata):
    """Mimic the (status, risk_level, metadata_data) row tuple.

    SQLAlchemy 2.x Row objects unpack as tuples in column-select order, so we
    return a plain tuple here instead of a namespace.
    """
    return (status, risk, metadata)


@pytest.fixture
def _run():
    """Run the async endpoint with a stub session that returns the given rows."""
    def _runner(rows):
        from services.registry.workspace import workspace_inventory

        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows

        class _Session:
            async def execute(self, _stmt):
                return _Result(rows)

        tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        return asyncio.new_event_loop().run_until_complete(
            workspace_inventory(db=_Session(), tenant_id=tenant_id),
        )

    return _runner


# ───────────────────────────────────────────────────────────────────────


def test_empty_workspace_returns_zeros(_run):
    resp = _run([])
    data = resp.data
    assert data["total"] == 0
    assert data["active"] == 0
    assert data["high_risk"] == 0
    assert data["wizard_provisioned"] == 0
    # Every known provider key is present (even when count is zero) so the
    # Dashboard can render every pie slice without a None check.
    for prov in ("anthropic", "openai", "bedrock", "langchain",
                 "cursor", "claude-code", "openhands", "custom", "unknown"):
        assert prov in data["by_provider"]
        assert data["by_provider"][prov] == 0


def test_by_provider_buckets_metadata_provider_tag(_run):
    rows = [
        _row("ACTIVE", "medium", {"provider": "anthropic", "wizard": True}),
        _row("ACTIVE", "medium", {"provider": "anthropic"}),
        _row("ACTIVE", "low", {"provider": "openai", "wizard": True}),
        _row("ACTIVE", "high", {"provider": "bedrock"}),
    ]
    data = _run(rows).data
    assert data["by_provider"]["anthropic"] == 2
    assert data["by_provider"]["openai"] == 1
    assert data["by_provider"]["bedrock"] == 1
    assert data["by_provider"]["unknown"] == 0


def test_unknown_provider_falls_to_unknown_bucket(_run):
    """Agents created before Sprint 2's wizard have no `provider` tag."""
    rows = [
        _row("ACTIVE", "low", None),
        _row("ACTIVE", "low", {}),
        _row("ACTIVE", "low", {"provider": "rogue-vendor"}),
    ]
    data = _run(rows).data
    assert data["by_provider"]["unknown"] == 3


def test_by_risk_counts_all_tiers(_run):
    rows = [
        _row("ACTIVE", "low", {}),
        _row("ACTIVE", "low", {}),
        _row("ACTIVE", "medium", {}),
        _row("ACTIVE", "high", {}),
        _row("ACTIVE", "critical", {}),
    ]
    data = _run(rows).data
    assert data["by_risk"]["low"] == 2
    assert data["by_risk"]["medium"] == 1
    assert data["by_risk"]["high"] == 1
    assert data["by_risk"]["critical"] == 1
    assert data["high_risk"] == 2  # high + critical


def test_status_rollups(_run):
    rows = [
        _row("ACTIVE", "low", {}),
        _row("ACTIVE", "low", {}),
        _row("QUARANTINED", "medium", {}),
        _row("TERMINATED", "low", {}),
    ]
    data = _run(rows).data
    assert data["active"] == 2
    assert data["quarantined"] == 1
    assert data["terminated"] == 1
    assert data["total"] == 4
    assert data["by_status"]["ACTIVE"] == 2
    assert data["by_status"]["QUARANTINED"] == 1
    assert data["by_status"]["TERMINATED"] == 1


def test_wizard_provisioned_only_counts_metadata_wizard_true(_run):
    rows = [
        _row("ACTIVE", "low", {"provider": "anthropic", "wizard": True}),
        _row("ACTIVE", "low", {"provider": "openai", "wizard": True}),
        _row("ACTIVE", "low", {"provider": "anthropic"}),  # no wizard tag
        _row("ACTIVE", "low", {}),  # no metadata at all
    ]
    data = _run(rows).data
    assert data["wizard_provisioned"] == 2


def test_total_matches_row_count_under_mixed_data(_run):
    """Sanity: the total field equals len(rows) even with messy metadata."""
    rows = [
        _row("ACTIVE", None, None),
        _row("ACTIVE", "low", "string-not-dict"),
        _row("QUARANTINED", "high", {"provider": ""}),
    ]
    data = _run(rows).data
    assert data["total"] == 3
