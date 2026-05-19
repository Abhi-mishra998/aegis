"""
Trust Score Engine — Feature 4.
================================
Continuously calculates per-agent + per-tenant trust scores from observed
edges. Deliberately additive / explainable: each component is bounded
[0, 1] so the operator can read the breakdown directly.

Algorithm (1.0 = perfect, 0.0 = untrusted):
  base               = 1.0
  − error_penalty    = 0.4 × (errors / total)          if total > 0
  − deny_penalty     = 0.3 × (deny / total)            if total > 0
  − risk_penalty     = 0.5 × avg_risk                  (avg risk ∈ [0,1])
  − burst_penalty    = 0.2  if max_risk ≥ 0.9 else 0
  − drift_penalty    = 0.3 × min(1, drift_score)
  clamped to [0, 1]

Inputs are pre-aggregated by GraphRepository.edge_stats() so each scoring
pass is O(1) DB calls per agent.
"""
from __future__ import annotations

from typing import Any


def compute_trust(stats: dict[str, Any], drift_score: float = 0.0) -> tuple[float, dict[str, float], str]:
    total = max(int(stats.get("total", 0)), 0)
    err = int(stats.get("error", 0))
    deny = int(stats.get("deny", 0))
    avg_risk = float(stats.get("avg_risk", 0.0) or 0.0)
    max_risk = float(stats.get("max_risk", 0.0) or 0.0)
    drift_score = float(drift_score or 0.0)

    error_rate = (err / total) if total else 0.0
    deny_rate = (deny / total) if total else 0.0

    error_penalty = 0.4 * min(1.0, error_rate)
    deny_penalty  = 0.3 * min(1.0, deny_rate)
    risk_penalty  = 0.5 * min(1.0, avg_risk)
    burst_penalty = 0.2 if max_risk >= 0.9 else 0.0
    drift_penalty = 0.3 * min(1.0, drift_score)

    raw = 1.0 - error_penalty - deny_penalty - risk_penalty - burst_penalty - drift_penalty
    score = max(0.0, min(1.0, raw))

    components = {
        "base": 1.0,
        "error_penalty": round(error_penalty, 4),
        "deny_penalty": round(deny_penalty, 4),
        "risk_penalty": round(risk_penalty, 4),
        "burst_penalty": round(burst_penalty, 4),
        "drift_penalty": round(drift_penalty, 4),
        "samples": float(total),
    }

    if score < 0.3:
        reason = "untrusted: dominant error/deny rate or sustained high risk"
    elif score < 0.6:
        reason = "degraded: penalties from drift / denied calls"
    elif score < 0.85:
        reason = "watch: minor anomalies"
    else:
        reason = "healthy"
    return round(score, 4), components, reason


def compute_tenant_trust(node_scores: list[float]) -> float:
    if not node_scores:
        return 1.0
    # Worst-90th-percentile — one rogue agent should drag the tenant down,
    # but a single outlier shouldn't dominate when there are many healthy agents.
    sorted_scores = sorted(node_scores)
    idx = max(0, int(len(sorted_scores) * 0.1) - 1)
    return round(sorted_scores[idx], 4)
