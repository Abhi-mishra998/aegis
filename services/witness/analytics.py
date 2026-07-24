"""ATF v3.2 §Phase 3 item 1 — Contradiction analytics.

Consumes Witness verdicts (any source: DB rows, event stream, replay
bundle) and produces per-agent, per-tool, per-tenant contradiction /
unobserved rates plus a ranked SOC triage queue.

Pure aggregator: caller supplies verdict records, module returns
dataclasses. No I/O, no DB, no HTTP. The gateway wires this to
`services/witness/store.py` (or a Redis Streams reader in production).

Ranks by a heuristic: raw contradiction count × (1 + unobserved ratio).
A high contradiction count is bad; a high unobserved ratio compounds
the uncertainty. `SOC_TRIAGE_HEAD_N` controls how many rows the SOC
dashboard renders.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

Verdict = Literal["CORROBORATED", "CONTRADICTED", "UNOBSERVED"]

SOC_TRIAGE_HEAD_N = 25

# Hard ceiling on the number of verdict records aggregated per call.
# Prevents a hostile /witness/analytics caller from streaming millions
# of records to OOM the worker. Legitimate ops dashboards aggregate
# tens of thousands per window (a 1-tenant 24-hour window at the §12.1
# reference workload has ~1M gated actions, but only C1-C3 get verdicts
# — that's ~150K verdicts). 250K is a generous ceiling for any real
# tenant; anything larger is either a config error or an attack.
_MAX_RECORDS = int(os.getenv("WITNESS_ANALYTICS_MAX_RECORDS", "250000"))


class AnalyticsInputTooLarge(Exception):
    """Raised when the caller passes more than `_MAX_RECORDS`. The
    router surfaces this as 413 so ops can distinguish it from a
    generic 500."""


@dataclass
class VerdictRecord:
    """Minimal shape — matches what `services/witness/router.py` emits."""

    tenant_id: str
    agent_id: str
    tool: str
    verdict: Verdict
    gate_decision_id: str = ""


@dataclass
class RateStats:
    total: int = 0
    corroborated: int = 0
    contradicted: int = 0
    unobserved: int = 0

    def add(self, v: Verdict) -> None:
        self.total += 1
        if v == "CORROBORATED":
            self.corroborated += 1
        elif v == "CONTRADICTED":
            self.contradicted += 1
        elif v == "UNOBSERVED":
            self.unobserved += 1

    @property
    def contradiction_ratio(self) -> float:
        return 0.0 if self.total == 0 else self.contradicted / self.total

    @property
    def unobserved_ratio(self) -> float:
        return 0.0 if self.total == 0 else self.unobserved / self.total


@dataclass
class TriageEntry:
    tenant_id: str
    agent_id: str
    tool: str
    stats: RateStats
    triage_score: float


@dataclass
class AnalyticsSnapshot:
    """One rollup pass — the compliance service returns this via
    `GET /witness/analytics`. The SOC dashboard renders `triage`."""

    per_tenant: dict[str, RateStats] = field(default_factory=dict)
    per_agent: dict[tuple[str, str], RateStats] = field(default_factory=dict)  # (tenant, agent)
    per_tool: dict[tuple[str, str], RateStats] = field(default_factory=dict)   # (tenant, tool)
    triage: list[TriageEntry] = field(default_factory=list)


def _triage_score(stats: RateStats) -> float:
    return stats.contradicted * (1.0 + stats.unobserved_ratio)


def aggregate(records: Iterable[VerdictRecord]) -> AnalyticsSnapshot:
    per_tenant: dict[str, RateStats] = defaultdict(RateStats)
    per_agent: dict[tuple[str, str], RateStats] = defaultdict(RateStats)
    per_tool: dict[tuple[str, str], RateStats] = defaultdict(RateStats)
    per_row: dict[tuple[str, str, str], RateStats] = defaultdict(RateStats)  # (tenant, agent, tool)

    processed = 0
    for r in records:
        processed += 1
        if processed > _MAX_RECORDS:
            raise AnalyticsInputTooLarge(
                f"analytics input exceeds cap {_MAX_RECORDS}"
            )
        per_tenant[r.tenant_id].add(r.verdict)
        per_agent[(r.tenant_id, r.agent_id)].add(r.verdict)
        per_tool[(r.tenant_id, r.tool)].add(r.verdict)
        per_row[(r.tenant_id, r.agent_id, r.tool)].add(r.verdict)

    triage = [
        TriageEntry(
            tenant_id=tenant_id,
            agent_id=agent_id,
            tool=tool,
            stats=stats,
            triage_score=_triage_score(stats),
        )
        for (tenant_id, agent_id, tool), stats in per_row.items()
        if stats.contradicted > 0 or stats.unobserved_ratio > 0.1
    ]
    triage.sort(key=lambda t: t.triage_score, reverse=True)

    return AnalyticsSnapshot(
        per_tenant=dict(per_tenant),
        per_agent=dict(per_agent),
        per_tool=dict(per_tool),
        triage=triage[:SOC_TRIAGE_HEAD_N],
    )


def top_offending_tools(snapshot: AnalyticsSnapshot, n: int = 5) -> list[tuple[str, str, int]]:
    """(tenant, tool, contradiction_count) — for the compliance heat map."""
    return sorted(
        [
            (tenant, tool, stats.contradicted)
            for (tenant, tool), stats in snapshot.per_tool.items()
            if stats.contradicted > 0
        ],
        key=lambda x: x[2],
        reverse=True,
    )[:n]


if __name__ == "__main__":
    rows = [
        VerdictRecord("acme", "ag_1", "crm.delete", "CORROBORATED"),
        VerdictRecord("acme", "ag_1", "crm.delete", "CORROBORATED"),
        VerdictRecord("acme", "ag_1", "crm.delete", "CONTRADICTED"),
        VerdictRecord("acme", "ag_2", "wire.send",  "CONTRADICTED"),
        VerdictRecord("acme", "ag_2", "wire.send",  "CONTRADICTED"),
        VerdictRecord("acme", "ag_2", "wire.send",  "UNOBSERVED"),
        VerdictRecord("acme", "ag_3", "read.pii",   "CORROBORATED"),
        VerdictRecord("beta", "ag_9", "crm.delete", "CONTRADICTED"),
    ]

    snap = aggregate(rows)
    assert snap.per_tenant["acme"].total == 7
    assert snap.per_tenant["acme"].contradicted == 3
    assert snap.per_agent[("acme", "ag_2")].contradicted == 2
    assert abs(snap.per_agent[("acme", "ag_2")].unobserved_ratio - 1/3) < 1e-9

    # Triage ranks ag_2/wire.send above ag_1/crm.delete (higher contradiction × unobserved)
    assert snap.triage[0].agent_id == "ag_2"
    assert snap.triage[0].tool == "wire.send"

    top = top_offending_tools(snap, n=3)
    assert top[0] == ("acme", "wire.send", 2)

    # Empty input is safe
    empty = aggregate([])
    assert empty.per_tenant == {}
    assert empty.triage == []

    # Counter still importable
    Counter()  # smoke

    print("witness_analytics OK")
