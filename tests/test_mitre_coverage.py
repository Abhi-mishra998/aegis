"""
Sprint 7 — Unit tests for GET /iag/mitre-coverage.

Exercises the endpoint directly (no FastAPI client) so the assertion
surface is the JSON shape the MitreCoverageGrid renders.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def _request_with_tenant():
    """Mimic FastAPI Request with the tenant_id our handler reads off
    request.state.
    """
    return SimpleNamespace(
        state=SimpleNamespace(tenant_id="11111111-1111-1111-1111-111111111111"),
        headers={"X-Tenant-ID": "11111111-1111-1111-1111-111111111111"},
    )


def _run():
    from services.gateway.routers.iag import get_mitre_coverage
    return asyncio.new_event_loop().run_until_complete(
        get_mitre_coverage(_request_with_tenant()),
    )


def test_returns_at_least_34_signals_across_multiple_tactics():
    data = _run()
    assert data["signal_total"] >= 34
    assert data["tactic_total"] >= 5  # at least 5 distinct tactics covered


def test_tactic_ids_are_well_formed():
    data = _run()
    for tactic in data["tactics"]:
        assert tactic["tactic_id"].startswith("TA"), tactic["tactic_id"]
        assert tactic["tactic_name"]  # non-empty
        assert tactic["technique_count"] == len(tactic["techniques"])


def test_every_technique_has_t_prefixed_id_and_at_least_one_signal():
    data = _run()
    for tactic in data["tactics"]:
        for technique in tactic["techniques"]:
            assert technique["technique_id"].startswith("T"), technique["technique_id"]
            assert technique["technique_name"]  # non-empty
            assert len(technique["signals"]) >= 1
            assert technique["max_severity"]
            assert 0 <= technique["max_score"] <= 100


def test_every_signal_has_canonical_response_action():
    data = _run()
    allowed_responses = {"allow", "monitor", "escalate", "deny", "quarantine"}
    for tactic in data["tactics"]:
        for technique in tactic["techniques"]:
            for sig in technique["signals"]:
                assert sig["default_response"] in allowed_responses, (
                    sig["id"], sig["default_response"],
                )
                assert 0 <= sig["default_score"] <= 100
                assert sig["description"]


def test_signal_total_equals_sum_of_per_technique_signal_counts():
    data = _run()
    counted = sum(
        len(t["signals"]) for tactic in data["tactics"] for t in tactic["techniques"]
    )
    assert counted == data["signal_total"]
