from __future__ import annotations

import warnings
from datetime import UTC, datetime

import structlog

from sdk.common.invariants import assert_risk_valid, clamp_risk
from services.decision.findings import (
    FINDING_POLICY_DENY,
    SIGNAL_THRESHOLDS,
    SIGNAL_TO_FINDING,
    validate_findings,
)
from services.decision.schemas import (
    Decision,
    DecisionContext,
    ExecutionAction,
    SignalEvaluation,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Weight Table
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS: dict[str, float] = {
    "inference": 0.35,
    "behavior": 0.30,
    "anomaly": 0.15,
    "cost": 0.10,
    "cross_agent": 0.10,
}

# ---------------------------------------------------------------------------
# Threshold Table
# ---------------------------------------------------------------------------

_THRESHOLDS = [
    (0.90, ExecutionAction.KILL),
    (0.70, ExecutionAction.ESCALATE),
    (0.50, ExecutionAction.THROTTLE),
    (0.30, ExecutionAction.MONITOR),
    (0.00, ExecutionAction.ALLOW),
]

# ---------------------------------------------------------------------------

def _classify_risk(score: float) -> str:
    if score >= 0.90:
        return "CRITICAL"
    if score >= 0.70:
        return "HIGH"
    if score >= 0.50:
        return "MEDIUM"
    if score >= 0.30:
        return "MONITOR"
    return "LOW"


def _action_from_score(score: float) -> ExecutionAction:
    for threshold, action in _THRESHOLDS:
        if score >= threshold:
            return action
    return ExecutionAction.ALLOW


# ---------------------------------------------------------------------------
# Decision Engine
# ---------------------------------------------------------------------------

class DecisionEngine:
    """
    Deterministic O(N) risk aggregation engine.

    Algorithm (7 steps, all deterministic):
      1. Clamp policy_risk_adjustment to [-0.30, +0.30] to prevent OPA scores
         from pushing the composite risk outside the signal range.
      2. Compute raw_score = weighted sum of 5 signals + safe_adjustment.
         Weights: inference=0.35, behavior=0.30, anomaly=0.15, cost=0.10,
         cross_agent=0.10 (must sum to 1.0 ± 0.001).
      3. Boost: if max(signals) ≥ 0.95 → raw_score ≥ 0.95;
                if max(signals) ≥ 0.80 → raw_score ≥ 0.60.
      4. Policy floor (hard, not overridable by learning):
         policy_allowed=False → raw_score ≥ 0.70.
      5. Learning discount (only when policy_allowed=True):
         raw_score -= min(fp_rate × 0.30, 0.20).
      6. Clamp to [0.0, 1.0] (4 decimal places).
      7. Map score to action via monotone threshold table:
         ≥0.90→KILL, ≥0.70→ESCALATE, ≥0.50→THROTTLE, ≥0.30→MONITOR, else→ALLOW.

    Invariants (testable via Hypothesis):
      - Output risk ∈ [0.0, 1.0] for any finite input.
      - policy_allowed=False → risk ≥ 0.70 AND action ∈ {ESCALATE,KILL}
        (learning discount does NOT apply when policy denies).
      - max_signal ≥ 0.95 AND fp_rate == 0 → risk ≥ 0.95.
      - All-zero signals, policy_allowed=True, fp_rate=0 → action=ALLOW.
      - Every element of findings is in CANONICAL_FINDINGS.
      - signals_evaluated contains exactly the 5 canonical signal keys.
      - NaN or ±inf inputs are coerced to 0.0 by float() before use.
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = weights or DEFAULT_WEIGHTS.copy()
        self._validate_weights()

    def _validate_weights(self) -> None:
        total = sum(self.weights.values())
        if not (0.999 < total < 1.001):
            raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")

    def evaluate(self, ctx: DecisionContext) -> Decision:

        w = self.weights

        # Step 1: Safe adjustment
        policy_adj = float(ctx.policy_risk_adjustment or 0.0)
        safe_adjustment = max(-0.3, min(0.3, policy_adj))

        # Ensure all risks are floats and non-None
        inf_risk = float(ctx.inference_risk or 0.0)
        beh_risk = float(ctx.behavior_risk or 0.0)
        ano_risk = float(ctx.anomaly_score or 0.0)
        cos_risk = float(ctx.cost_risk or 0.0)
        cro_risk = float(ctx.cross_agent_risk or 0.0)

        raw_score = (
            (inf_risk * w["inference"]) +
            (beh_risk * w["behavior"]) +
            (ano_risk * w["anomaly"]) +
            (cos_risk * w["cost"]) +
            (cro_risk * w["cross_agent"]) +
            safe_adjustment
        )

        signals = {
            "inference": round(inf_risk, 4),
            "behavior": round(beh_risk, 4),
            "anomaly": round(ano_risk, 4),
            "cost": round(cos_risk, 4),
            "cross_agent": round(cro_risk, 4),
            "policy_adjustment": round(policy_adj, 4),
        }

        # Step 2: Boosting
        max_signal = max(signals.values())
        if max_signal >= 0.95:
            raw_score = max(raw_score, 0.95)
        elif max_signal >= 0.80:
            raw_score = max(raw_score, 0.60)

        # Step 3: Policy floor (HARD RULE - cannot be soft-overridden)
        if not ctx.policy_allowed:
            raw_score = max(raw_score, 0.70)

        # Step 4: Learning adjustment (ONLY if policy allows - hard rules exempt from soft discounts)
        if ctx.policy_allowed:
            fp_rate = float(ctx.false_positive_rate or 0.0)
            if fp_rate > 0.0:
                discount = min(fp_rate * 0.3, 0.20)
                raw_score = max(0.0, raw_score - discount)
        # Note: When policy denies, soft learning discounts do NOT apply to prevent bypass of hard security rules

        # Step 5: Clamp
        final_score = clamp_risk(raw_score)

        assert_risk_valid(final_score, context=f"agent={ctx.agent_id} tool={ctx.tool}")

        # Step 6: Action — with the 2026-06-15 hard-deny override.
        # The threshold table doesn't have a DENY band; it goes 0.70=ESCALATE,
        # 0.90=KILL. That's why $25M wires above hard cap returned ESCALATE
        # — the policy denial drove the score to 0.95+ but landed in KILL or
        # ESCALATE. For policy rules tagged `hard_deny`, force DENY so the
        # gateway returns a real block instead of an approval-required.
        if ctx.policy_hard_deny:
            action = ExecutionAction.DENY
            final_score = max(final_score, 0.90)
        else:
            action = _action_from_score(final_score)

        # Step 7: Build the canonical-vocabulary `findings` + the diagnostic
        # `signals_evaluated` map. Sprint 2.2 (2026-05-15):
        #
        #   - `findings` carries ONLY canonical-vocabulary strings, and only
        #     for signals that crossed their trigger threshold. A clean call
        #     returns []. Customer security teams trust this list because it
        #     contains real conclusions, not "we ran the data-exfil
        #     classifier and it returned 0.15".
        #   - `signals_evaluated` carries every classifier's score +
        #     threshold + triggered bit. That answers the diagnostic
        #     question "did we evaluate behaviour?" — distinct from
        #     "what did we conclude?".
        #   - Raw `behavior_flags` / `inference_flags` from upstream
        #     classifiers go into `metadata.diagnostic_flags`, NOT into
        #     findings. They're useful for debugging but they're noise to
        #     a customer reading the response.
        signal_scores = {
            "inference":   inf_risk,
            "behavior":    beh_risk,
            "anomaly":     ano_risk,
            "cost":        cos_risk,
            "cross_agent": cro_risk,
        }
        signals_evaluated: dict[str, SignalEvaluation] = {}
        triggered_findings: list[str] = []
        for sig_name, score in signal_scores.items():
            threshold = SIGNAL_THRESHOLDS[sig_name]
            # Engine emits a finding when score is STRICTLY ABOVE threshold —
            # exactly the previous semantics (`if inf_risk > 0.60:` etc.) so
            # threshold tuning stays observable.
            triggered = score > threshold
            signals_evaluated[sig_name] = SignalEvaluation(
                score=round(score, 4),
                threshold=threshold,
                triggered=triggered,
            )
            if triggered:
                finding_name = SIGNAL_TO_FINDING[sig_name]
                if finding_name not in triggered_findings:
                    triggered_findings.append(finding_name)

        # Policy-side findings (OPA deny). The free-form `policy_reason`
        # detail is preserved in metadata for forensic replay; the
        # *finding* surface stays canonical.
        if not ctx.policy_allowed:
            if FINDING_POLICY_DENY not in triggered_findings:
                triggered_findings.append(FINDING_POLICY_DENY)

        findings = validate_findings(triggered_findings)
        risk_level = _classify_risk(final_score)

        logger.info(
            "decision_evaluated",
            agent_id=str(ctx.agent_id),
            tenant_id=str(ctx.tenant_id),
            tool=ctx.tool,
            action=action.value,
            risk_score=final_score,
            risk_level=risk_level,
            signals=signals,
        )

        # Diagnostic-only context for forensics — never affects routing.
        raw_diagnostic_flags = list(ctx.behavior_flags or []) + list(ctx.inference_flags or [])

        # DeprecationWarning emitted to signal customer-facing removal in v2.0 — see ROADMAP.md
        warnings.warn(
            "The 'reasons' field is deprecated; use 'findings'",
            DeprecationWarning,
            stacklevel=2,
        )

        return Decision(
            action=action,
            risk=final_score,
            confidence=ctx.confidence,
            findings=findings,
            signals_evaluated=signals_evaluated,
            reasons=findings,            # DEPRECATED alias kept for back-compat
            signals=signals,
            metadata={
                "risk_level":          risk_level,
                "weights":             w,
                "components":          signals,
                "policy_risk_adj":     round(policy_adj, 4),
                "policy_reason":       ctx.policy_reason,
                "diagnostic_flags":    raw_diagnostic_flags,
                "timestamp":           datetime.now(tz=UTC).isoformat(),
            },
        )

# ---------------------------------------------------------------------------

decision_engine = DecisionEngine()
