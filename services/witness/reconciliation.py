"""ATF v3.2 §6 + Security SLO I1 — Reconciliation.

'100% of C2/C3 executed actions carry a Gate decision record'
(§12.3 I1). Continuous reconciliation between Gate decisions and
Witness verdicts is how we KNOW that invariant holds. This module
computes the gap; the ops layer runs it on a schedule.

Pure function. Caller supplies:

    gate_decisions   iter[GateDecisionRow]   — from audit_logs
    verdicts         iter[WitnessVerdictRow] — from services/witness/store

Returns a `ReconciliationReport` naming any gate_decision_id that
should have a Witness verdict but does not (an UNOBSERVED entry is
written for the gap so it's visible in the export bundle).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

ActionClass = Literal["C0", "C1", "C2", "C3"]


@dataclass(frozen=True)
class GateDecisionRow:
    gate_decision_id: str
    action_class: ActionClass
    ts: str


@dataclass(frozen=True)
class WitnessVerdictRow:
    gate_decision_id: str
    verdict: Literal["CORROBORATED", "CONTRADICTED", "UNOBSERVED"]


@dataclass
class ReconciliationReport:
    total_gate_decisions: int
    total_c2_c3: int
    matched: int
    unmatched_ids: list[str] = field(default_factory=list)
    unmatched_c2_c3_ids: list[str] = field(default_factory=list)

    @property
    def match_ratio(self) -> float:
        """Fraction of gate decisions that also have a Witness verdict.
        SLO target: 100% for C2/C3."""
        return 0.0 if self.total_gate_decisions == 0 else self.matched / self.total_gate_decisions

    @property
    def c2_c3_match_ratio(self) -> float:
        matched_c2_c3 = self.total_c2_c3 - len(self.unmatched_c2_c3_ids)
        return 0.0 if self.total_c2_c3 == 0 else matched_c2_c3 / self.total_c2_c3


def reconcile(
    gate_decisions: Iterable[GateDecisionRow],
    verdicts: Iterable[WitnessVerdictRow],
) -> ReconciliationReport:
    """Compute the reconciliation gap. Ops SHOULD alert when
    `c2_c3_match_ratio < 1.0` — that's an I1 breach."""
    verdict_ids: set[str] = {v.gate_decision_id for v in verdicts}
    gate_rows = list(gate_decisions)

    matched = 0
    unmatched: list[str] = []
    unmatched_c2_c3: list[str] = []
    total_c2_c3 = 0

    for row in gate_rows:
        if row.action_class in ("C2", "C3"):
            total_c2_c3 += 1
        if row.gate_decision_id in verdict_ids:
            matched += 1
            continue
        unmatched.append(row.gate_decision_id)
        if row.action_class in ("C2", "C3"):
            unmatched_c2_c3.append(row.gate_decision_id)

    return ReconciliationReport(
        total_gate_decisions=len(gate_rows),
        total_c2_c3=total_c2_c3,
        matched=matched,
        unmatched_ids=unmatched,
        unmatched_c2_c3_ids=unmatched_c2_c3,
    )


def synthesize_unobserved_verdicts(
    report: ReconciliationReport,
) -> list[WitnessVerdictRow]:
    """Turn each C2/C3 gap into an explicit UNOBSERVED verdict row so
    the ledger + export bundle honestly say 'we didn't see this'
    instead of silently omitting.
    """
    return [
        WitnessVerdictRow(gate_decision_id=gid, verdict="UNOBSERVED")
        for gid in report.unmatched_c2_c3_ids
    ]


if __name__ == "__main__":
    gate = [
        GateDecisionRow("gd_1", "C0", "2026-07-21T14:00Z"),
        GateDecisionRow("gd_2", "C2", "2026-07-21T14:01Z"),
        GateDecisionRow("gd_3", "C3", "2026-07-21T14:02Z"),
        GateDecisionRow("gd_4", "C2", "2026-07-21T14:03Z"),
    ]
    verdicts = [
        WitnessVerdictRow("gd_2", "CORROBORATED"),
        WitnessVerdictRow("gd_3", "CONTRADICTED"),
        # gd_4 missing — a C2 gap → I1 breach
    ]

    report = reconcile(gate, verdicts)
    assert report.total_gate_decisions == 4
    assert report.total_c2_c3 == 3
    assert report.matched == 2
    assert report.unmatched_ids == ["gd_1", "gd_4"]
    assert report.unmatched_c2_c3_ids == ["gd_4"]
    assert abs(report.c2_c3_match_ratio - 2/3) < 1e-9
    assert report.match_ratio == 0.5

    synth = synthesize_unobserved_verdicts(report)
    assert len(synth) == 1
    assert synth[0].gate_decision_id == "gd_4"
    assert synth[0].verdict == "UNOBSERVED"

    # Empty inputs safe
    empty = reconcile([], [])
    assert empty.match_ratio == 0.0
    assert empty.c2_c3_match_ratio == 0.0
    assert synthesize_unobserved_verdicts(empty) == []

    # Perfect coverage → 100%
    perfect_gate = [GateDecisionRow("gd_x", "C3", "2026-07-21T15:00Z")]
    perfect_verdict = [WitnessVerdictRow("gd_x", "CORROBORATED")]
    perfect = reconcile(perfect_gate, perfect_verdict)
    assert perfect.c2_c3_match_ratio == 1.0

    print("reconciliation OK")
