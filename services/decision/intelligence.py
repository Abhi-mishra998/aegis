from __future__ import annotations

import json
from typing import Any

import structlog
from groq import AsyncGroq

from sdk.common.config import settings
from services.decision.schemas import Decision, DecisionContext, ExecutionAction

logger = structlog.get_logger(__name__)

# Hot-path uses the fastest model — this runs inline in the request lifecycle
# with a 5-second total SLA budget, so latency beats quality here.
_MODEL_FAST = settings.GROQ_MODEL_FAST      # llama-3.1-8b-instant  (~50-100ms)
_MODEL_DEEP = settings.GROQ_MODEL           # llama-3.3-70b-versatile

# Risk threshold above which we invoke the larger model for a second opinion
_DEEP_ANALYSIS_THRESHOLD = 0.75

from sdk.common.groq_helpers import ENFORCEMENT_SYSTEM_PROMPT as _SYSTEM_PROMPT, ENFORCEMENT_USER_TEMPLATE as _USER_TEMPLATE  # noqa: E402


class GroqSecurityBrain:
    """
    Inline AI security brain — runs in the hot path with a tight timeout.
    Uses llama-3.1-8b-instant (fast model) for all events, upgrading to the
    deeper model only when composite risk is above _DEEP_ANALYSIS_THRESHOLD.
    """

    def __init__(self, api_key: str) -> None:
        self._client = AsyncGroq(api_key=api_key)

    async def evaluate(self, ctx: DecisionContext, heuristic: Decision) -> Decision:
        """
        Asks Groq to validate or override the heuristic decision.
        Falls back to the heuristic on any error (fail-open for latency safety).
        """
        model = _MODEL_DEEP if heuristic.risk >= _DEEP_ANALYSIS_THRESHOLD else _MODEL_FAST

        user_msg = _USER_TEMPLATE.format(
            tool=ctx.tool,
            inference_risk=getattr(ctx, "inference_risk", 0.0),
            behavior_risk=getattr(ctx, "behavior_risk", 0.0),
            anomaly_score=getattr(ctx, "anomaly_score", 0.0),
            cost_risk=getattr(ctx, "cost_risk", 0.0),
            cross_agent_risk=getattr(ctx, "cross_agent_risk", 0.0),
            composite_risk=heuristic.risk,
            behavior_flags=ctx.behavior_flags or [],
            inference_flags=ctx.inference_flags or [],
            heuristic_action=heuristic.action.value,
            heuristic_risk=heuristic.risk,
            heuristic_confidence=heuristic.confidence,
            heuristic_reasons=heuristic.reasons or [],
        )

        try:
            completion = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=300,
            )

            result: dict[str, Any] = json.loads(
                completion.choices[0].message.content
            )

            action_str = result.get("recommended_action", heuristic.action.value).lower()
            try:
                final_action = ExecutionAction(action_str)
            except ValueError:
                final_action = heuristic.action

            logger.info(
                "ai_brain_verdict",
                model=model,
                heuristic=heuristic.action.value,
                ai_verdict=final_action.value,
                threat=result.get("threat_classification"),
                ai_confidence=result.get("confidence"),
            )

            return Decision(
                action=final_action,
                risk=heuristic.risk,
                confidence=float(result.get("confidence", heuristic.confidence)),
                findings=heuristic.findings,
                reasons=[
                    f"AI ({model}): {result.get('narrative', 'Validated')}",
                    *heuristic.reasons,
                ],
                signals=heuristic.signals,
                signals_evaluated=heuristic.signals_evaluated,
                metadata={
                    "ai_override": final_action != heuristic.action,
                    "brain_model": model,
                    "threat_classification": result.get("threat_classification"),
                    **heuristic.metadata,
                },
            )

        except Exception as exc:
            logger.error("groq_brain_error", model=model, error=str(exc))
            return heuristic  # fail-open: preserve heuristic on timeout / API error

    async def close(self) -> None:
        await self._client.close()
