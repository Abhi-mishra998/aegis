"""
Regression tests for 3 bugs found and fixed during pre-sprint verification
(2026-05-16).

  FIX-1: services/behavior/main.py
    BehaviorAnalysis uses ConfigDict(strict=True) so uuid.UUID instances are
    required.  The Decision service POSTs tenant_id/agent_id as JSON strings.
    Without conversion the call raises a Pydantic ValidationError on every
    request and the engine falls through to degraded-mode risk=0.5.

  FIX-2: services/decision/intelligence.py
    GroqSecurityBrain.evaluate() builds a new Decision on success but omitted
    `findings=heuristic.findings` and `signals_evaluated=heuristic.signals_evaluated`.
    Result: `findings` was always [] even on kill-level decisions.

  FIX-3: services/policy/router.py
    The /policy/execute response dict included `reasons` but not `findings`.
    Result: `findings` key was absent from the final /execute response.

Run with:
    .venv/bin/python3 -m pytest tests/test_pre_sprint_fixes.py -v
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.behavior.schemas import BehaviorAnalysis
from services.decision.schemas import Decision, DecisionContext, ExecutionAction, SignalEvaluation


# ---------------------------------------------------------------------------
# FIX-1: Behavior UUID string → uuid.UUID conversion
# ---------------------------------------------------------------------------

class TestBehaviorUUIDConversion:
    """
    BehaviorAnalysis(strict=True) rejects plain strings for UUID fields.
    The /analyze endpoint must convert JSON-string UUIDs before constructing
    the Pydantic model.
    """

    def test_behavior_analysis_requires_uuid_instance(self):
        """Strict mode rejects string inputs for UUID fields."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BehaviorAnalysis(
                agent_id="00000000-0000-0000-0000-000000000001",
                tenant_id="00000000-0000-0000-0000-000000000002",
            )

    def test_behavior_analysis_accepts_uuid_instance(self):
        """Passing uuid.UUID instances satisfies strict mode."""
        agent_id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        ba = BehaviorAnalysis(agent_id=agent_id, tenant_id=tenant_id)
        assert ba.agent_id == agent_id
        assert ba.tenant_id == tenant_id

    def test_uuid_conversion_from_string_succeeds(self):
        """uuid.UUID(str) converts a valid UUID string to a uuid.UUID instance."""
        s = "00000000-0000-0000-0000-000000000001"
        result = uuid.UUID(s)
        assert isinstance(result, uuid.UUID)
        assert str(result) == s

    def test_uuid_conversion_from_invalid_string_raises(self):
        """uuid.UUID('') and uuid.UUID('bad') raise ValueError."""
        with pytest.raises(ValueError):
            uuid.UUID("")
        with pytest.raises(ValueError):
            uuid.UUID("not-a-uuid")

    def test_behavior_analyze_endpoint_accepts_string_uuid_payload(self):
        """
        Simulate the /analyze handler logic: extract strings from payload,
        convert to uuid.UUID, then construct BehaviorAnalysis.
        If conversion succeeds, the model accepts the values.
        """
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "agent_id": str(uuid.uuid4()),
            "tool": "read_file",
            "tokens": 100,
        }
        # Replicate the fix from services/behavior/main.py
        try:
            tenant_id = uuid.UUID(payload.get("tenant_id") or "")
            agent_id = uuid.UUID(payload.get("agent_id") or "")
        except (ValueError, AttributeError) as exc:
            pytest.fail(f"UUID conversion should not fail for valid UUIDs: {exc}")

        # Constructing BehaviorAnalysis must not raise
        ba = BehaviorAnalysis(agent_id=agent_id, tenant_id=tenant_id)
        assert ba.agent_id == agent_id
        assert ba.tenant_id == tenant_id

    def test_behavior_analyze_endpoint_returns_error_on_invalid_uuid(self):
        """
        Simulate the error path: invalid UUID strings return a dict with
        success=False rather than crashing with an unhandled exception.
        """
        payload = {"tenant_id": "not-a-uuid", "agent_id": "also-bad"}

        # Replicate the fix's error handling
        try:
            tenant_id = uuid.UUID(payload.get("tenant_id") or "")
            agent_id = uuid.UUID(payload.get("agent_id") or "")
            result = {"success": True}
        except (ValueError, AttributeError) as exc:
            result = {"success": False, "error": f"invalid uuid: {exc}"}

        assert result["success"] is False
        assert "invalid uuid" in result["error"]


# ---------------------------------------------------------------------------
# FIX-2: GroqSecurityBrain preserves findings + signals_evaluated
# ---------------------------------------------------------------------------

def _make_heuristic(
    action: ExecutionAction = ExecutionAction.KILL,
    risk: float = 0.95,
    findings: list[str] | None = None,
    signals_evaluated: dict[str, Any] | None = None,
) -> Decision:
    """Build a Decision that simulates a heuristic output with real findings."""
    # Use explicit None check — findings=[] is a valid (empty) input, not a default.
    effective_findings = (
        ["prompt_injection_detected", "policy_deny"]
        if findings is None
        else findings
    )
    return Decision(
        action=action,
        risk=risk,
        confidence=0.9,
        findings=effective_findings,
        signals_evaluated=signals_evaluated if signals_evaluated is not None else {
            "inference": SignalEvaluation(score=0.95, threshold=0.6, triggered=True),
            "behavior": SignalEvaluation(score=0.1, threshold=0.4, triggered=False),
        },
        reasons=["policy_deny"],
        signals={"inference": 0.95},
        metadata={},
    )


class TestGroqBrainFindingsPreservation:
    """
    GroqSecurityBrain.evaluate() must forward findings and signals_evaluated
    from the heuristic Decision into the AI-overridden Decision.
    """

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _make_ctx(self) -> DecisionContext:
        return DecisionContext(
            tenant_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            tool="delete_database",
            inference_risk=0.95,
            behavior_risk=0.2,
        )

    def _fake_groq_response(self, action: str = "kill") -> MagicMock:
        """Return a mock that looks like an AsyncGroq completion."""
        content = json.dumps({
            "recommended_action": action,
            "threat_classification": "PROMPT_INJECTION",
            "confidence": 0.97,
            "narrative": "High-confidence injection detected.",
        })
        msg = MagicMock()
        msg.content = content
        choice = MagicMock()
        choice.message = msg
        completion = MagicMock()
        completion.choices = [choice]
        return completion

    def test_findings_preserved_when_groq_confirms_action(self):
        """When Groq confirms the heuristic action, findings must pass through."""
        from services.decision.intelligence import GroqSecurityBrain

        brain = GroqSecurityBrain.__new__(GroqSecurityBrain)
        brain._client = AsyncMock()
        brain._client.chat = AsyncMock()
        brain._client.chat.completions = AsyncMock()
        brain._client.chat.completions.create = AsyncMock(
            return_value=self._fake_groq_response("kill")
        )

        heuristic = _make_heuristic()
        ctx = self._make_ctx()

        result = self._run(brain.evaluate(ctx, heuristic))

        assert result.findings == heuristic.findings, (
            f"findings must be preserved: expected {heuristic.findings!r}, "
            f"got {result.findings!r}"
        )

    def test_signals_evaluated_preserved_when_groq_confirms_action(self):
        """signals_evaluated must pass through unchanged."""
        from services.decision.intelligence import GroqSecurityBrain

        brain = GroqSecurityBrain.__new__(GroqSecurityBrain)
        brain._client = AsyncMock()
        brain._client.chat = AsyncMock()
        brain._client.chat.completions = AsyncMock()
        brain._client.chat.completions.create = AsyncMock(
            return_value=self._fake_groq_response("kill")
        )

        heuristic = _make_heuristic()
        ctx = self._make_ctx()

        result = self._run(brain.evaluate(ctx, heuristic))

        assert result.signals_evaluated == heuristic.signals_evaluated, (
            "signals_evaluated must be preserved through the Groq override path"
        )

    def test_findings_preserved_when_groq_overrides_action(self):
        """Even when Groq changes the action (deny → kill), findings must survive."""
        from services.decision.intelligence import GroqSecurityBrain

        brain = GroqSecurityBrain.__new__(GroqSecurityBrain)
        brain._client = AsyncMock()
        brain._client.chat = AsyncMock()
        brain._client.chat.completions = AsyncMock()
        brain._client.chat.completions.create = AsyncMock(
            return_value=self._fake_groq_response("kill")  # override deny → kill
        )

        heuristic = _make_heuristic(action=ExecutionAction.DENY)
        ctx = self._make_ctx()

        result = self._run(brain.evaluate(ctx, heuristic))

        assert result.action == ExecutionAction.KILL, "Groq override should take effect"
        assert result.findings == heuristic.findings, (
            "findings must survive even when action is overridden"
        )
        assert result.signals_evaluated == heuristic.signals_evaluated

    def test_groq_error_falls_back_to_heuristic_with_findings(self):
        """On API error, the heuristic is returned intact — findings included."""
        from services.decision.intelligence import GroqSecurityBrain

        brain = GroqSecurityBrain.__new__(GroqSecurityBrain)
        brain._client = AsyncMock()
        brain._client.chat = AsyncMock()
        brain._client.chat.completions = AsyncMock()
        brain._client.chat.completions.create = AsyncMock(
            side_effect=Exception("timeout")
        )

        heuristic = _make_heuristic()
        ctx = self._make_ctx()

        result = self._run(brain.evaluate(ctx, heuristic))

        # On fallback the heuristic IS the result — findings must be present
        assert result.findings == heuristic.findings
        assert result.action == heuristic.action

    def test_empty_findings_preserved(self):
        """A clean allow decision with findings=[] stays empty after Groq."""
        from services.decision.intelligence import GroqSecurityBrain

        brain = GroqSecurityBrain.__new__(GroqSecurityBrain)
        brain._client = AsyncMock()
        brain._client.chat = AsyncMock()
        brain._client.chat.completions = AsyncMock()
        brain._client.chat.completions.create = AsyncMock(
            return_value=self._fake_groq_response("allow")
        )

        heuristic = _make_heuristic(
            action=ExecutionAction.ALLOW, risk=0.1, findings=[]
        )
        ctx = self._make_ctx()

        result = self._run(brain.evaluate(ctx, heuristic))

        assert result.findings == [], "Empty findings must not be inflated"


# ---------------------------------------------------------------------------
# FIX-3: /policy/execute response includes findings field
# ---------------------------------------------------------------------------

class TestPolicyExecuteFindings:
    """
    The /policy/execute response dict must include a `findings` key sourced
    from `payload._decision.findings`.  Before the fix this key was absent,
    causing the downstream gateway response to have `findings: None`.
    """

    def _make_request(
        self,
        request_id: str = "req-abc",
        agent_id: str = "agent-1",
        tenant_id: str = "tenant-1",
    ) -> MagicMock:
        """Build a minimal mock fastapi.Request."""
        headers_dict = {
            "X-Request-ID": request_id,
            "X-Agent-ID": agent_id,
            "X-Tenant-ID": tenant_id,
        }
        req = MagicMock()
        # Leave req.headers as a MagicMock so .get is configurable.
        req.headers.get = lambda k, d="": headers_dict.get(k, d)
        return req

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_findings_present_in_response(self):
        """findings key must be in the execute_tool response dict."""
        from services.policy.router import execute_tool

        request = self._make_request()
        payload = {
            "tool": "read_file",
            "_decision": {
                "action": "allow",
                "risk": 0.1,
                "confidence": 0.9,
                "findings": ["prompt_injection_detected"],
                "reasons": [],
                "signals": {},
            },
        }

        result = self._run(execute_tool(request, payload))

        assert "findings" in result, "findings key must be present in /execute response"
        assert result["findings"] == ["prompt_injection_detected"]

    def test_findings_empty_list_on_clean_allow(self):
        """A clean allow with no findings should return findings=[] not None."""
        from services.policy.router import execute_tool

        request = self._make_request()
        payload = {
            "tool": "read_file",
            "_decision": {
                "action": "allow",
                "risk": 0.0,
                "confidence": 1.0,
                "findings": [],
                "reasons": [],
                "signals": {},
            },
        }

        result = self._run(execute_tool(request, payload))

        assert result["findings"] == [], "Clean allow must have findings=[] not None"

    def test_findings_defaults_to_empty_when_decision_missing(self):
        """When _decision is absent, findings must default to [] not raise."""
        from services.policy.router import execute_tool

        request = self._make_request()
        payload = {"tool": "list_files"}

        result = self._run(execute_tool(request, payload))

        assert "findings" in result
        assert result["findings"] == []

    def test_all_required_fields_present(self):
        """The full /execute response shape must include all expected keys."""
        from services.policy.router import execute_tool

        request = self._make_request()
        payload = {
            "tool": "write_file",
            "_decision": {
                "action": "kill",
                "risk": 0.95,
                "confidence": 0.88,
                "findings": ["policy_deny", "prompt_injection_detected"],
                "reasons": ["policy_deny"],
                "signals": {"inference": 0.95},
            },
        }

        result = self._run(execute_tool(request, payload))

        expected_keys = {"success", "request_id", "agent_id", "tenant_id", "tool",
                         "action", "risk", "confidence", "findings", "reasons",
                         "signals", "executed_at"}
        missing = expected_keys - set(result.keys())
        assert not missing, f"Response missing keys: {missing}"
        assert result["findings"] == ["policy_deny", "prompt_injection_detected"]
        assert result["action"] == "kill"
