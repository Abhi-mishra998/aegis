"""
Sprint 4 — Storyline reconstruction (pure-Python).

Takes an ordered sequence of Step records (one per finding) and produces a
Storyline dataclass that the SOC sees in /incidents/{id}.

Design rules:

  1. Pure function. No I/O. No Redis. No DB. Easy to unit-test.
  2. Deterministic. Same input → same output. No clock, no random.
  3. Consecutive-dedup the technique chain so repeated reads don't pollute
     the timeline.
  4. Status comes from the highest-tier step seen, not the most recent.
     A `deny` early followed by `monitor` later keeps `blocked` status —
     the incident did its job, the subsequent monitor calls are noise.
  5. blocked_at_step = seq of the FIRST step that crossed the deny line.

Adding new fields:
  * If it's per-step: add to Step.
  * If it's incident-wide: add to Storyline + recompute it in build().

The Storyline JSON shape is the contract /incidents/{id} returns. Adding
fields here is a forward-compatible change (consumers ignore unknown keys),
removing them is a contract break.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Tier vocabulary — mirrors local_action_semantics.py constants.
# ---------------------------------------------------------------------------
TIER_ALLOW      = "allow"
TIER_MONITOR    = "monitor"
TIER_ESCALATE   = "escalate"
TIER_DENY       = "deny"
TIER_QUARANTINE = "quarantine"

# Severity rank — used to decide which step is the "final" outcome of the
# incident. Higher rank wins.
_TIER_RANK = {
    TIER_ALLOW:      0,
    TIER_MONITOR:    1,
    TIER_ESCALATE:   2,
    TIER_DENY:       3,
    TIER_QUARANTINE: 4,
}

# Status the incident lives in. open → blocked / quarantined when a deny-or-
# higher finding lands. `resolved` is operator-set (out of scope for Sprint 4).
STATUS_OPEN        = "open"
STATUS_BLOCKED     = "blocked"
STATUS_QUARANTINED = "quarantined"
STATUS_RESOLVED    = "resolved"


# ---------------------------------------------------------------------------
# Human MITRE-tactic labels for title generation. Keep aligned with
# SecurityObjective values in signal_registry.py.
# ---------------------------------------------------------------------------
_TACTIC_LABEL = {
    "TA0001": "Initial Access",
    "TA0003": "Persistence",
    "TA0004": "Privilege Escalation",
    "TA0005": "Defense Evasion",
    "TA0006": "Credential Access",
    "TA0007": "Discovery",
    "TA0009": "Collection",
    "TA0010": "Exfiltration",
    "TA0040": "Impact",
}


@dataclass(frozen=True)
class Step:
    """One observation appended to an Incident."""
    seq:              int        # 1-indexed
    ts:               float      # unix seconds
    agent_id:         str
    signal_id:        str        # registered signal name (e.g. "external_pii_exfil")
    mitre_tactic:     str        # e.g. "TA0010"
    mitre_technique:  str        # full label, e.g. "T1567.002 Exfiltration to Web Service"
    objective:        str        # SecurityObjective.value, e.g. "exfiltration"
    tier:             str        # one of the TIER_* constants
    policy_id:        str        # e.g. "SEC-EXFIL-001", "" if monitor/allow
    target:           str        # what was touched (table, file, host, "" if N/A)
    explanation:      str        # one-line analyst-readable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Storyline:
    """The aggregated story a SOC analyst opens."""
    incident_id:           str
    tenant_id:             str
    status:                str
    start_ts:              float
    last_event_ts:         float
    participating_agents:  list[str]
    primary_session_id:    str
    mitre_tactic_chain:    list[str]
    mitre_technique_chain: list[str]
    objective_chain:       list[str]
    steps:                 list[Step]
    blocked_at_step:       int | None       # seq of the step that denied
    blocking_policy_id:    str
    title:                 str
    narrative:             str
    risk_score:            int              # max per-step risk_score, 0-100

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Steps are dataclasses → already dictified by asdict. Force list copies
        # so the consumer can't mutate our internal lists by reference.
        d["steps"]                 = list(d["steps"])
        d["participating_agents"]  = list(d["participating_agents"])
        d["mitre_tactic_chain"]    = list(d["mitre_tactic_chain"])
        d["mitre_technique_chain"] = list(d["mitre_technique_chain"])
        d["objective_chain"]       = list(d["objective_chain"])
        return d


def _dedup_consecutive(seq: Iterable[str]) -> list[str]:
    """Keep order; drop a value when it's the same as the previous one."""
    out: list[str] = []
    prev: str | None = None
    for v in seq:
        if v and v != prev:
            out.append(v)
            prev = v
    return out


def _title_for(tactic_chain: list[str]) -> str:
    """Human-readable arrow chain of MITRE tactic labels, truncated to 5."""
    if not tactic_chain:
        return "Storyline pending"
    labels = [_TACTIC_LABEL.get(t, t) for t in tactic_chain[:5]]
    if len(tactic_chain) > 5:
        labels.append("…")
    return " → ".join(labels)


def _narrative_for(steps: list[Step]) -> str:
    """Per-step one-line narrative, joined with newlines."""
    lines: list[str] = []
    for s in steps:
        agent_short = s.agent_id[:8] if s.agent_id else "?"
        target_part = f" target={s.target}" if s.target else ""
        tier_part   = f" [{s.tier}]" if s.tier and s.tier != TIER_ALLOW else ""
        # Prefer the explanation when present; fall back to the signal id.
        body = s.explanation or s.signal_id
        lines.append(
            f"Step {s.seq} ({s.mitre_technique}{tier_part}): "
            f"agent {agent_short}…{target_part} — {body}"
        )
    return "\n".join(lines)


def build(
    *,
    incident_id:        str,
    tenant_id:          str,
    steps:              list[Step],
    primary_session_id: str = "",
    max_risk_score:     int = 0,
) -> Storyline:
    """Reconstruct the Storyline from an ordered (or unordered) list of steps.

    Caller passes steps with monotonically increasing `seq` already assigned.
    If they aren't sorted we sort here so the output is deterministic.
    """
    if not steps:
        return Storyline(
            incident_id=incident_id, tenant_id=tenant_id, status=STATUS_OPEN,
            start_ts=0.0, last_event_ts=0.0,
            participating_agents=[], primary_session_id=primary_session_id,
            mitre_tactic_chain=[], mitre_technique_chain=[], objective_chain=[],
            steps=[], blocked_at_step=None, blocking_policy_id="",
            title=_title_for([]), narrative="", risk_score=max_risk_score,
        )

    ordered = sorted(steps, key=lambda s: (s.ts, s.seq))

    # Chains — dedup CONSECUTIVE only.
    tactic_chain    = _dedup_consecutive(s.mitre_tactic for s in ordered)
    technique_chain = _dedup_consecutive(s.mitre_technique for s in ordered)
    objective_chain = _dedup_consecutive(s.objective for s in ordered)

    # Participating agents — preserve first-seen order.
    seen_agents: list[str] = []
    seen_agent_set: set[str] = set()
    for s in ordered:
        if s.agent_id and s.agent_id not in seen_agent_set:
            seen_agents.append(s.agent_id)
            seen_agent_set.add(s.agent_id)

    # Status from the highest-rank tier seen.
    worst = max(ordered, key=lambda s: _TIER_RANK.get(s.tier, 0))
    worst_rank = _TIER_RANK.get(worst.tier, 0)
    if worst_rank >= _TIER_RANK[TIER_QUARANTINE]:
        status = STATUS_QUARANTINED
    elif worst_rank >= _TIER_RANK[TIER_DENY]:
        status = STATUS_BLOCKED
    else:
        status = STATUS_OPEN

    # blocked_at_step = seq of the first deny-or-higher step.
    blocked_at_step: int | None = None
    blocking_policy_id = ""
    for s in ordered:
        if _TIER_RANK.get(s.tier, 0) >= _TIER_RANK[TIER_DENY]:
            blocked_at_step = s.seq
            blocking_policy_id = s.policy_id or ""
            break

    return Storyline(
        incident_id=incident_id,
        tenant_id=tenant_id,
        status=status,
        start_ts=ordered[0].ts,
        last_event_ts=ordered[-1].ts,
        participating_agents=seen_agents,
        primary_session_id=primary_session_id,
        mitre_tactic_chain=tactic_chain,
        mitre_technique_chain=technique_chain,
        objective_chain=objective_chain,
        steps=ordered,
        blocked_at_step=blocked_at_step,
        blocking_policy_id=blocking_policy_id,
        title=_title_for(tactic_chain),
        narrative=_narrative_for(ordered),
        risk_score=max(max_risk_score, max(
            (_TIER_RANK.get(s.tier, 0) * 25 for s in ordered), default=0
        )),
    )
