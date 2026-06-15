"""
Sprint 6 — Pure-Python shadow-policy rule evaluator.

Mirrors services/policy/router.py::_eval_condition / _simulate_decision
EXACTLY — the rule shape is the same `PolicyRule = {conditions, action,
description}` the existing /policy/simulate endpoint already uses. We
duplicate the evaluator here (rather than import) so the audit service
isn't coupled to the policy service's import order, and so the shadow
evaluator can run inside the gateway's fire-and-forget background task
without an extra service hop.

Contract
========
`evaluate_rules(rules, context)` returns:

    ShadowEvalResult(
        action: str,                  # allow | deny | throttle | escalate | monitor
        matched_rule_index: int | None,
        matched_rule_description: str,
        latency_ms: float,
    )

Allow is the default — same as the live engine.

The evaluator is INTENTIONALLY simple: no I/O, no globals, no logging.
Both the shadow gateway hook and the would-have-denied report-builder
call this function with the same rule shape so a shadow result is
identical regardless of where it was produced.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


_NUMERIC_FIELDS = {
    "risk_score",
    "inference_risk",
    "behavior_risk",
    "anomaly_score",
}

_STRING_FIELDS = {
    "tool",
    "agent_id",
    "tenant_id",
    "payload_substring",
}


@dataclass(frozen=True)
class ShadowEvalResult:
    """Outcome of running one set of shadow rules against one /execute."""

    action:                   str
    matched_rule_index:       int | None
    matched_rule_description: str
    latency_ms:               float


def _coerce_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v)


def _eval_condition(cond: dict, context: dict) -> bool:
    """One condition vs the request context.

    Conditions are `{field, operator, value}` dicts (the existing
    PolicyCondition shape). Unknown fields evaluate False — we never
    accidentally match on a typo.

    The `payload_substring` field is the only one that does substring
    containment; everything else is exact match (strings) or numeric
    comparison (gt/gte/lt/lte/eq/neq).
    """
    field = str(cond.get("field", ""))
    operator = str(cond.get("operator", "eq")).lower()
    raw_value = cond.get("value")

    if field == "payload_substring":
        needle = _coerce_str(raw_value).lower()
        haystack = _coerce_str(context.get("payload")).lower()
        if operator == "contains":
            return needle in haystack
        if operator == "not_contains":
            return needle not in haystack
        return False

    if field in _NUMERIC_FIELDS:
        actual = _coerce_float(context.get(field))
        expected = _coerce_float(raw_value)
        if actual is None or expected is None:
            return False
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        if operator == "eq":
            return actual == expected
        if operator == "neq":
            return actual != expected
        return False

    if field in _STRING_FIELDS:
        actual = _coerce_str(context.get(field))
        expected = _coerce_str(raw_value)
        if operator == "eq":
            return actual == expected
        if operator == "neq":
            return actual != expected
        return False

    return False


def _normalise_action(raw: Any) -> str:
    """Canonicalise the rule's `action` into the runtime vocabulary."""
    a = _coerce_str(raw).lower().strip()
    if a in {"allow", "monitor"}:
        return "allow"
    if a == "deny":
        return "deny"
    if a == "throttle":
        return "throttle"
    if a == "escalate":
        return "escalate"
    if a == "kill":
        return "deny"  # kill collapses to deny at the shadow surface
    return "allow"


def evaluate_rules(
    rules: list[dict],
    context: dict[str, Any],
) -> ShadowEvalResult:
    """First-matching-rule wins — same semantics as /policy/simulate."""
    t0 = time.perf_counter()
    for idx, rule in enumerate(rules or []):
        conditions = rule.get("conditions") or []
        if not conditions:
            # A rule with no conditions is effectively the default branch;
            # apply it.
            return ShadowEvalResult(
                action=_normalise_action(rule.get("action")),
                matched_rule_index=idx,
                matched_rule_description=str(rule.get("description", ""))[:255],
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
        if all(_eval_condition(c, context) for c in conditions):
            return ShadowEvalResult(
                action=_normalise_action(rule.get("action")),
                matched_rule_index=idx,
                matched_rule_description=str(rule.get("description", ""))[:255],
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
    return ShadowEvalResult(
        action="allow",
        matched_rule_index=None,
        matched_rule_description="",
        latency_ms=(time.perf_counter() - t0) * 1000.0,
    )


def is_drift(real_action: str, shadow_action: str) -> bool:
    """Drift = real != shadow on the canonical allow/deny vocab.

    A shadow-deny against a real-allow is the headline FP signal — the
    candidate policy would have blocked legitimate traffic. A shadow-
    allow against a real-deny is the FN signal — the candidate would
    have let an attack through.
    """
    return _normalise_action(real_action) != _normalise_action(shadow_action)


def would_have_blocked_benign(
    real_action: str, shadow_action: str
) -> bool:
    """The single most important metric for go/no-go on promotion.

    Real pipeline allowed (legitimate traffic); shadow policy would have
    denied. If this count is non-zero for any candidate policy, the
    operator must NOT promote it without investigating each case.
    """
    real = _normalise_action(real_action)
    shadow = _normalise_action(shadow_action)
    return real == "allow" and shadow in {"deny", "throttle", "escalate"}
