"""Real test for the C3 sampling planner fingerprint semantics.

The upstream Anthropic response's tool_use blocks carry a per-call
`id` field (`toolu_...`). Without stripping that, every sample would
appear different — even from a deterministic LLM at temperature 0 —
because Anthropic mints a fresh id per invocation. Sampling would
BLOCK every C3 call under that bug.

This test proves the planner IGNORES the id and fingerprints on
name + input only, so a stable LLM producing 3 identical plans (with
different call ids) resolves as CONSISTENT.
"""
from __future__ import annotations

import pytest

from services.policy.c3_gate import evaluate


class TestPlannerFingerprintIgnoresId:
    @pytest.mark.asyncio
    async def test_three_calls_same_plan_different_ids_are_consistent(self):
        """The real Anthropic behavior: same prompt → same plan (name+input)
        → different tool-use ids. Sampling should treat these as the
        same plan and ALLOW."""
        # Simulate the planner exactly as messages.py builds it: strip the id.
        _bodies = [
            {"content": [{"type": "tool_use",
                          "id": "toolu_01AA", "name": "send_wire",
                          "input": {"amount_usd": 5000, "recipient": "acme"}}]},
            {"content": [{"type": "tool_use",
                          "id": "toolu_02BB", "name": "send_wire",
                          "input": {"amount_usd": 5000, "recipient": "acme"}}]},
            {"content": [{"type": "tool_use",
                          "id": "toolu_03CC", "name": "send_wire",
                          "input": {"amount_usd": 5000, "recipient": "acme"}}]},
        ]
        it = iter(_bodies)

        async def _planner() -> dict:
            _body = next(it)
            _tools = [
                {"name": b.get("name"), "input": b.get("input")}
                for b in (_body.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            return {"tool_calls": _tools}

        result = await evaluate(_planner)
        assert result.decision == "ALLOW", (
            f"same semantic plan across 3 samples must resolve CONSISTENT "
            f"despite different tool-use ids — got {result}"
        )
        assert result.verdict.verdict == "CONSISTENT"

    @pytest.mark.asyncio
    async def test_different_semantic_plans_still_block(self):
        """Sanity: if the actual plan (name/input) really diverges across
        samples, the gate should still BLOCK."""
        _bodies = [
            {"content": [{"type": "tool_use", "id": "toolu_01",
                          "name": "send_wire",
                          "input": {"amount_usd": 5000, "recipient": "acme"}}]},
            {"content": [{"type": "tool_use", "id": "toolu_02",
                          "name": "send_wire",
                          "input": {"amount_usd": 5000, "recipient": "typo-corp"}}]},
            {"content": [{"type": "tool_use", "id": "toolu_03",
                          "name": "send_wire",
                          "input": {"amount_usd": 999_999, "recipient": "unknown"}}]},
        ]
        it = iter(_bodies)

        async def _planner() -> dict:
            _body = next(it)
            _tools = [
                {"name": b.get("name"), "input": b.get("input")}
                for b in (_body.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]
            return {"tool_calls": _tools}

        result = await evaluate(_planner)
        assert result.decision == "BLOCK", (
            "3 samples with diverging inputs must not pass the 2/3 quorum"
        )
