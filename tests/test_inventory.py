"""
Sprint 4 — Unit tests for the workspace inventory aggregator.

Post-Q31 refactor: the endpoint now aggregates SERVER-SIDE via 4 SQL
queries (status GROUP BY, risk GROUP BY, provider GROUP BY, wizard
COUNT). Prior version loaded every row into python which OOM'd on
tenants with >100k agents. Tests feed pre-aggregated shapes.
"""
from __future__ import annotations

import asyncio
import uuid
from collections import Counter
from typing import Any
from unittest.mock import MagicMock


def _rows_to_aggregates(rows: list[tuple[Any, Any, Any]]) -> dict[str, Any]:
    """Turn old-style (status, risk, metadata) row list into the four
    aggregate shapes the SQL endpoint now expects. Lets each test keep
    its readable per-row fixture."""
    status_counter: Counter = Counter()
    risk_counter: Counter = Counter()
    provider_counter: Counter = Counter()
    wizard_count = 0
    for status, risk, metadata in rows:
        status_counter[str(status)] += 1
        risk_counter[str(risk or "low").lower()] += 1
        meta = metadata if isinstance(metadata, dict) else {}
        prov = str(meta.get("provider") or "").lower().strip()
        provider_counter[prov] += 1
        w = meta.get("wizard")
        # Mirror the SQL WHERE: present and not the string "false" / "".
        if w is not None and w is not False and str(w).lower() != "false" and str(w) != "":
            wizard_count += 1
    return {
        "status":   list(status_counter.items()),
        "risk":     list(risk_counter.items()),
        "provider": list(provider_counter.items()),
        "wizard":   wizard_count,
    }


def _mk_result(all_rows=None, scalar=None):
    r = MagicMock()
    if all_rows is not None:
        r.all.return_value = all_rows
    if scalar is not None:
        r.scalar_one.return_value = scalar
    return r


def _run(rows):
    from services.registry.workspace import workspace_inventory
    agg = _rows_to_aggregates(rows)

    scripted = [
        _mk_result(all_rows=agg["status"]),
        _mk_result(all_rows=agg["risk"]),
        _mk_result(all_rows=agg["provider"]),
        _mk_result(scalar=agg["wizard"]),
    ]

    class _Session:
        def __init__(self):
            self._scripted = list(scripted)
        async def execute(self, _stmt, _params=None):
            return self._scripted.pop(0)

    tenant_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    return asyncio.new_event_loop().run_until_complete(
        workspace_inventory(db=_Session(), tenant_id=tenant_id),
    )


def _row(status, risk, metadata):
    return (status, risk, metadata)


# ───────────────────────────────────────────────────────────────────────


def test_empty_workspace_returns_zeros():
    data = _run([]).data
    assert data["total"] == 0
    assert data["active"] == 0
    assert data["high_risk"] == 0
    assert data["wizard_provisioned"] == 0
    for prov in ("anthropic", "openai", "bedrock", "langchain",
                 "cursor", "claude-code", "openhands", "custom", "unknown"):
        assert prov in data["by_provider"]
        assert data["by_provider"][prov] == 0


def test_by_provider_buckets_metadata_provider_tag():
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


def test_unknown_provider_falls_to_unknown_bucket():
    """Agents created before Sprint 2's wizard have no `provider` tag."""
    rows = [
        _row("ACTIVE", "low", None),
        _row("ACTIVE", "low", {}),
        _row("ACTIVE", "low", {"provider": "rogue-vendor"}),
    ]
    data = _run(rows).data
    assert data["by_provider"]["unknown"] == 3


def test_by_risk_counts_all_tiers():
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


def test_status_rollups():
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


def test_wizard_provisioned_only_counts_metadata_wizard_true():
    rows = [
        _row("ACTIVE", "low", {"provider": "anthropic", "wizard": True}),
        _row("ACTIVE", "low", {"provider": "openai", "wizard": True}),
        _row("ACTIVE", "low", {"provider": "anthropic"}),  # no wizard tag
        _row("ACTIVE", "low", {}),  # no metadata at all
    ]
    data = _run(rows).data
    assert data["wizard_provisioned"] == 2


def test_total_matches_row_count_under_mixed_data():
    """Sanity: total counts every status bucket."""
    rows = [
        _row("ACTIVE", None, None),
        _row("ACTIVE", "low", "string-not-dict"),
        _row("QUARANTINED", "high", {"provider": ""}),
    ]
    data = _run(rows).data
    assert data["total"] == 3


def test_uses_sql_aggregation_not_row_load():
    """Q31 regression: the endpoint MUST NOT re-introduce the unbounded
    `select(status, risk_level, metadata_data).all()` pattern."""
    import inspect

    from services.registry import workspace as _mod
    src = inspect.getsource(_mod.workspace_inventory)
    # The unbounded triple-column select is gone.
    assert "select(Agent.status, Agent.risk_level, Agent.metadata_data)" not in src
    # Server-side aggregation is in place.
    assert "func.count()" in src
    assert "group_by" in src
