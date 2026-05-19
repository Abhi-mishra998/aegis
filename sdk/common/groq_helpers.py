"""
Shared Groq enrichment helpers used by both services.insight.worker and
services.groq_worker.service. Both consumers share the same model-selection
and signal-formatting logic; keeping it in one place prevents drift.
"""
from __future__ import annotations

import json
from typing import Any

from sdk.common.config import settings

HIGH_RISK_THRESHOLD = 0.65
MODEL_DEEP = settings.GROQ_MODEL        # llama-3.3-70b-versatile (best quality)
MODEL_FAST = settings.GROQ_MODEL_FAST   # llama-3.1-8b-instant  (low-risk events)

# ---------------------------------------------------------------------------
# Shared prompt templates — canonical single source of truth
# ---------------------------------------------------------------------------

# Used by: services.insight.worker, services.groq_worker.service
THREAT_INTEL_SYSTEM_PROMPT = """\
You are an enterprise AI security analyst producing threat intelligence briefings \
for an AI agent governance platform. You analyze blocked or high-risk agent \
tool-execution events and generate concise, actionable security insights for \
executive review.

Return ONLY valid JSON — no markdown, no explanation text outside the JSON object.\
"""

THREAT_INTEL_USER_TEMPLATE = """\
Analyze the following AI agent security event and produce threat intelligence.

EVENT DETAILS:
  Agent ID  : {agent_id}
  Tool      : {tool}
  Decision  : {decision}  (enforced by the security engine)
  Risk Score: {risk_score:.3f}

SIGNALS (if available):
  {signals_block}

Return exactly this JSON schema:
{{
  "root_cause": "<1-sentence root cause analysis>",
  "threat_classification": "PROMPT_INJECTION|DATA_EXFILTRATION|COST_ABUSE|COORDINATED_ATTACK|ANOMALOUS_BEHAVIOR|POLICY_VIOLATION|BENIGN_ANOMALY",
  "recommendation": "HIGHLIGHT|MONITOR|THROTTLE|ESCALATE|TERMINATE",
  "confidence": "HIGH|MEDIUM|LOW",
  "narrative": "<2-3 sentence executive summary of the threat and recommended response>"
}}\
"""

# Used by: services.decision.intelligence (real-time enforcement, not post-hoc)
ENFORCEMENT_SYSTEM_PROMPT = """\
You are an AI security firewall making real-time enforcement decisions for an \
enterprise agent governance platform. Your output directly controls whether an \
AI agent's tool call is blocked, monitored, or allowed.

Rules:
1. Only override the heuristic decision when there is strong signal to do so.
2. Never downgrade a KILL or DENY to ALLOW without clear justification.
3. If the heuristic is sound, confirm it — do not change for the sake of changing.
4. Your recommended_action is the final action sent to the enforcement layer.
5. Respond with ONLY valid JSON — no markdown, no explanation text outside the JSON.\
"""

ENFORCEMENT_USER_TEMPLATE = """\
Analyze this AI agent tool-execution event and return your enforcement verdict.

RISK SIGNALS (0.0 = safe, 1.0 = critical):
  tool              : {tool}
  inference_risk    : {inference_risk:.3f}  (prompt injection / tool guard)
  behavior_risk     : {behavior_risk:.3f}  (velocity, sequences, loops)
  anomaly_score     : {anomaly_score:.3f}
  cost_risk         : {cost_risk:.3f}
  cross_agent_risk  : {cross_agent_risk:.3f}
  composite_risk    : {composite_risk:.3f}

FLAGS:
  behavior : {behavior_flags}
  inference: {inference_flags}

HEURISTIC DECISION: {heuristic_action} (risk={heuristic_risk:.3f}, confidence={heuristic_confidence:.2f})
HEURISTIC REASONS : {heuristic_reasons}

Return exactly this JSON schema:
{{
  "recommended_action": "allow|monitor|throttle|escalate|deny|kill",
  "threat_classification": "PROMPT_INJECTION|DATA_EXFILTRATION|COST_ABUSE|COORDINATED_ATTACK|ANOMALOUS_BEHAVIOR|POLICY_VIOLATION|BENIGN",
  "confidence": <float 0.0-1.0>,
  "narrative": "<one sentence plain-English verdict>"
}}\
"""


def pick_model(risk_score: float) -> str:
    return MODEL_DEEP if risk_score >= HIGH_RISK_THRESHOLD else MODEL_FAST


def build_signals_block(event: dict[str, Any]) -> str:
    raw = event.get("signals", "")
    if not raw:
        return "not available"
    try:
        signals = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        if isinstance(signals, dict):
            return "  ".join(
                f"{k}={v:.3f}"
                for k, v in signals.items()
                if isinstance(v, (int, float))
            )
        return str(raw)
    except Exception:
        return str(raw)[:200]
