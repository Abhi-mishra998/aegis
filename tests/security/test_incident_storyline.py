"""
Sprint 4 — Storyline reconstruction unit tests.

All tests target the pure-function builder in storyline.py. No Redis, no DB.
These are the algorithmic correctness checks.
"""
from __future__ import annotations

from services.security.incidents.storyline import (
    Step,
    STATUS_BLOCKED,
    STATUS_OPEN,
    STATUS_QUARANTINED,
    build,
)


def _step(seq, ts, agent, signal, tactic, tech, obj, tier, pid="", target="", expl=""):
    return Step(
        seq=seq, ts=ts, agent_id=agent, signal_id=signal,
        mitre_tactic=tactic, mitre_technique=tech, objective=obj,
        tier=tier, policy_id=pid, target=target, explanation=expl,
    )


# ───── Grouping ─────────────────────────────────────────────────────────────

def test_storyline_empty_steps_returns_open_with_empty_chains():
    s = build(incident_id="INC-X", tenant_id="t", steps=[])
    assert s.status == STATUS_OPEN
    assert s.steps == []
    assert s.mitre_tactic_chain == []
    assert s.objective_chain == []
    assert s.blocked_at_step is None
    assert s.title == "Storyline pending"


def test_storyline_orders_steps_by_ts():
    """Steps passed out of order get sorted by ts before chain construction."""
    s_late = _step(2, 100.0, "agA", "external_pii_exfil", "TA0010",
                    "T1567.002 Exfiltration to Web Service", "exfiltration",
                    "deny", "SEC-EXFIL-001")
    s_early = _step(1, 50.0, "agA", "schema_recon", "TA0007",
                     "T1087 Account Discovery", "discovery", "monitor")
    s = build(incident_id="INC-1", tenant_id="t", steps=[s_late, s_early])
    assert s.mitre_tactic_chain == ["TA0007", "TA0010"]
    assert s.steps[0].seq == 1  # the early one is first


# ───── MITRE chain dedup ────────────────────────────────────────────────────

def test_storyline_dedups_consecutive_tactic_repeats():
    """Three back-to-back bulk_pii reads = one entry in the tactic chain."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007",
              "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(3, 30, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(4, 40, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(5, 50, "agA", "external_pii_exfil", "TA0010",
              "T1567.002", "exfiltration", "deny", "SEC-EXFIL-001"),
    ]
    s = build(incident_id="INC-2", tenant_id="t", steps=steps)
    assert s.mitre_tactic_chain == ["TA0007", "TA0009", "TA0010"]
    assert s.mitre_technique_chain == ["T1087", "T1213", "T1567.002"]
    # The full per-step list is still preserved.
    assert len(s.steps) == 5


def test_storyline_does_not_dedup_non_consecutive_repeats():
    """A repeated tactic after an intervening other tactic counts twice."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(3, 30, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
    ]
    s = build(incident_id="INC-3", tenant_id="t", steps=steps)
    assert s.mitre_tactic_chain == ["TA0007", "TA0009", "TA0007"]


# ───── Status transitions ──────────────────────────────────────────────────

def test_storyline_status_open_when_only_monitor():
    steps = [_step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor")]
    s = build(incident_id="INC-O", tenant_id="t", steps=steps)
    assert s.status == STATUS_OPEN
    assert s.blocked_at_step is None


def test_storyline_status_blocked_when_any_deny():
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "money_transfer_above_hard_cap", "TA0040",
              "T1657", "impact", "deny", "FIN-WIRE-001"),
        _step(3, 30, "agA", "external_get", "TA0007", "T1133", "discovery", "allow"),
    ]
    s = build(incident_id="INC-B", tenant_id="t", steps=steps)
    assert s.status == STATUS_BLOCKED
    assert s.blocked_at_step == 2
    assert s.blocking_policy_id == "FIN-WIRE-001"


def test_storyline_status_quarantined_wins_over_blocked():
    """A quarantine step anywhere → status quarantined even if a later step is monitor."""
    steps = [
        _step(1, 10, "agA", "attack_chain_match", "TA0010", "T1020",
              "exfiltration", "quarantine", "SEC-CHAIN-DENY-001"),
        _step(2, 20, "agA", "external_get", "TA0007", "T1133", "discovery", "allow"),
    ]
    s = build(incident_id="INC-Q", tenant_id="t", steps=steps)
    assert s.status == STATUS_QUARANTINED


def test_storyline_blocked_at_step_picks_first_deny():
    """When multiple deny steps land, blocked_at_step is the FIRST one."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "destructive_sql_ddl", "TA0040", "T1485", "impact",
              "deny", "SEC-SQL-001"),
        _step(3, 30, "agA", "destructive_shell_command", "TA0040", "T1485",
              "impact", "deny", "SEC-SHELL-001"),
    ]
    s = build(incident_id="INC-MULTI", tenant_id="t", steps=steps)
    assert s.blocked_at_step == 2
    assert s.blocking_policy_id == "SEC-SQL-001"


# ───── Participating agents ────────────────────────────────────────────────

def test_storyline_unions_distinct_agents():
    """Cross-agent swarm: 4 agents, each a step, all in one storyline."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agB", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(3, 30, "agC", "compression_for_exfil", "TA0009", "T1560",
              "collection", "monitor"),
        _step(4, 40, "agD", "external_pii_exfil", "TA0010", "T1567.002",
              "exfiltration", "deny", "SEC-EXFIL-001"),
    ]
    s = build(incident_id="INC-S", tenant_id="t", steps=steps)
    assert s.participating_agents == ["agA", "agB", "agC", "agD"]
    assert s.mitre_tactic_chain == ["TA0007", "TA0009", "TA0010"]
    assert s.status == STATUS_BLOCKED


def test_storyline_dedups_repeat_agent():
    """One agent firing 3 findings = 1 entry in participating_agents."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
    ]
    s = build(incident_id="INC-DA", tenant_id="t", steps=steps)
    assert s.participating_agents == ["agA"]


# ───── Title generation ────────────────────────────────────────────────────

def test_storyline_title_uses_human_labels():
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(3, 30, "agA", "external_pii_exfil", "TA0010", "T1567.002",
              "exfiltration", "deny", "SEC-EXFIL-001"),
    ]
    s = build(incident_id="INC-T", tenant_id="t", steps=steps)
    assert s.title == "Discovery → Collection → Exfiltration"


def test_storyline_title_truncates_long_chains_to_5_plus_ellipsis():
    chain = [
        ("TA0001", "T1190", "initial_access"),
        ("TA0003", "T1098.004", "persistence"),
        ("TA0004", "T1098.001", "privilege_escalation"),
        ("TA0005", "T1070.002", "defense_evasion"),
        ("TA0006", "T1552.001", "credential_access"),
        ("TA0007", "T1087", "discovery"),
        ("TA0009", "T1213", "collection"),
    ]
    steps = [
        _step(i + 1, 10 * (i + 1), "agA", f"sig{i}", t, tech, obj, "monitor")
        for i, (t, tech, obj) in enumerate(chain)
    ]
    s = build(incident_id="INC-LONG", tenant_id="t", steps=steps)
    assert s.title.endswith("…")
    assert s.title.count("→") == 5


# ───── Narrative ────────────────────────────────────────────────────────────

def test_storyline_narrative_has_one_line_per_step():
    steps = [
        _step(1, 10, "agentABC12345", "schema_recon", "TA0007", "T1087 Account Discovery",
              "discovery", "monitor", target="customers"),
        _step(2, 20, "agentABC12345", "external_pii_exfil", "TA0010",
              "T1567.002 Exfiltration", "exfiltration", "deny", "SEC-EXFIL-001",
              target="transfer.sh", expl="POST to known exfil host with PII body"),
    ]
    s = build(incident_id="INC-N", tenant_id="t", steps=steps)
    lines = s.narrative.split("\n")
    assert len(lines) == 2
    assert "Step 1" in lines[0]
    assert "T1087" in lines[0]
    assert "customers" in lines[0]
    assert "Step 2" in lines[1]
    assert "[deny]" in lines[1]
    assert "transfer.sh" in lines[1]


# ───── Risk score ──────────────────────────────────────────────────────────

def test_storyline_risk_score_uses_max_tier_rank():
    """Quarantine (rank 4) × 25 = 100; deny (3) × 25 = 75; etc."""
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
        _step(2, 20, "agA", "bulk_pii_egress_above_threshold", "TA0009",
              "T1213", "collection", "escalate"),
        _step(3, 30, "agA", "attack_chain_match", "TA0010", "T1020",
              "exfiltration", "quarantine", "SEC-CHAIN-DENY-001"),
    ]
    s = build(incident_id="INC-R", tenant_id="t", steps=steps)
    assert s.risk_score == 100


def test_storyline_risk_score_honours_explicit_max_floor():
    steps = [
        _step(1, 10, "agA", "schema_recon", "TA0007", "T1087", "discovery", "monitor"),
    ]
    # Monitor alone → tier rank 1 × 25 = 25. Floor of 80 wins.
    s = build(incident_id="INC-F", tenant_id="t", steps=steps, max_risk_score=80)
    assert s.risk_score == 80
