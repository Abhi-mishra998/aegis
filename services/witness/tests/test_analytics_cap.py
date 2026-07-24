"""Test that `aggregate` refuses inputs larger than the DoS ceiling."""
from __future__ import annotations

import pytest

from services.witness import analytics as an


def _mk_record(i: int) -> an.VerdictRecord:
    return an.VerdictRecord(
        tenant_id=f"t{i % 10}",
        agent_id=f"a{i % 100}",
        tool=f"tool{i % 20}",
        verdict="CORROBORATED",
    )


class TestInputSizeCap:
    def test_under_cap_ok(self, monkeypatch):
        monkeypatch.setattr(an, "_MAX_RECORDS", 100)
        snap = an.aggregate(_mk_record(i) for i in range(50))
        assert snap.per_tenant  # non-empty result

    def test_at_cap_ok(self, monkeypatch):
        monkeypatch.setattr(an, "_MAX_RECORDS", 100)
        snap = an.aggregate(_mk_record(i) for i in range(100))
        assert sum(s.total for s in snap.per_tenant.values()) == 100

    def test_over_cap_raises(self, monkeypatch):
        monkeypatch.setattr(an, "_MAX_RECORDS", 100)
        with pytest.raises(an.AnalyticsInputTooLarge, match="exceeds cap 100"):
            an.aggregate(_mk_record(i) for i in range(101))

    def test_default_cap_env_readable(self):
        """The cap is env-tunable — a customer with legitimate 500K
        verdicts can set WITNESS_ANALYTICS_MAX_RECORDS at deploy time."""
        # Default > 100K covers real §12.1 workloads.
        assert an._MAX_RECORDS >= 100_000

    def test_streaming_input_aborted_at_ceiling(self, monkeypatch):
        """Even an infinite generator is bounded — we don't materialize
        the whole list, we abort mid-iteration. Prevents an attacker
        streaming forever and OOMing the worker."""
        monkeypatch.setattr(an, "_MAX_RECORDS", 50)

        def _endless():
            i = 0
            while True:
                yield _mk_record(i)
                i += 1

        with pytest.raises(an.AnalyticsInputTooLarge):
            an.aggregate(_endless())
