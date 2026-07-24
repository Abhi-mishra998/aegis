"""Real tests for the C3 consistency-sampling gate. No mocks of the
sampling logic — the planner is a small async callable and we exercise
all three verdict paths + the opt-in guard."""
from __future__ import annotations

import pytest

from services.policy.c3_gate import evaluate, should_sample


class TestShouldSample:
    def test_off_when_action_class_not_c3(self, monkeypatch):
        monkeypatch.setenv("ACP_C3_SAMPLING_TENANTS", "acme")
        assert not should_sample("C2", "acme")
        assert not should_sample("C1", "acme")
        assert not should_sample("C0", "acme")

    def test_off_when_tenant_not_enabled(self, monkeypatch):
        monkeypatch.setenv("ACP_C3_SAMPLING_TENANTS", "beta")
        assert not should_sample("C3", "acme")

    def test_on_when_c3_and_enabled(self, monkeypatch):
        monkeypatch.setenv("ACP_C3_SAMPLING_TENANTS", "acme,beta")
        assert should_sample("C3", "acme")
        assert should_sample("C3", "beta")

    def test_off_when_env_var_missing(self, monkeypatch):
        monkeypatch.delenv("ACP_C3_SAMPLING_TENANTS", raising=False)
        assert not should_sample("C3", "acme")


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_consistent_returns_allow_with_winning_plan(self):
        stable_plan = {"tool": "wire.send", "amount": 5_000, "recipient": "acme"}

        async def planner() -> dict:
            return dict(stable_plan)

        result = await evaluate(planner)
        assert result.decision == "ALLOW"
        assert result.verdict.verdict == "CONSISTENT"
        assert result.winning_plan == stable_plan

    @pytest.mark.asyncio
    async def test_two_of_three_agree_returns_allow(self):
        seq = [
            {"tool": "wire.send", "amount": 5_000, "recipient": "acme"},
            {"tool": "wire.send", "amount": 5_000, "recipient": "acme"},
            {"tool": "wire.send", "amount": 5_000, "recipient": "TYPO-CORP"},
        ]
        it = iter(seq)

        async def planner() -> dict:
            return next(it)

        result = await evaluate(planner)
        assert result.decision == "ALLOW"
        assert result.verdict.dominant_count == 2
        assert result.winning_plan["recipient"] == "acme"

    @pytest.mark.asyncio
    async def test_all_three_differ_blocks_as_needs_human(self):
        seq = [
            {"tool": "wire.send", "amount": 1_000},
            {"tool": "wire.send", "amount": 2_000},
            {"tool": "wire.send", "amount": 3_000},
        ]
        it = iter(seq)

        async def planner() -> dict:
            return next(it)

        result = await evaluate(planner)
        assert result.decision == "BLOCK"
        assert result.verdict.verdict == "NEEDS_HUMAN"
        assert result.winning_plan is None

    @pytest.mark.asyncio
    async def test_inconsistent_blocks(self):
        seq = [
            {"tool": "a", "x": 1},
            {"tool": "a", "x": 1},
            {"tool": "b", "x": 2},
            {"tool": "b", "x": 2},
        ]
        it = iter(seq)

        async def planner() -> dict:
            return next(it)

        result = await evaluate(planner, samples=4, quorum=3)
        assert result.decision == "BLOCK"
        assert result.verdict.verdict == "INCONSISTENT"

    @pytest.mark.asyncio
    async def test_planner_error_propagates(self):
        """Silent swallow of planner errors would defeat the point."""

        async def planner() -> dict:
            raise RuntimeError("upstream LLM down")

        with pytest.raises(RuntimeError, match="upstream LLM down"):
            await evaluate(planner)
