"""ATF v3.2 §4.2 (SCIM agent extension) + I5 invariant.

Reconciler: for every registered agent, resolve `human_responsible`
against the tenant SCIM directory. Orphans → QUARANTINED per §9.1.

Pure function against a `scim_lookup` callable + an iterable of agent
records; no HTTP, no DB. The gateway wires the callable to a real SCIM
client. Daily cron (or lifecycle ROTATE trigger) invokes.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

ScimStatus = Literal["ACTIVE", "SUSPENDED", "NOT_FOUND"]
ReconcileAction = Literal["OK", "QUARANTINE", "RESTORE"]


@dataclass
class AgentRecord:
    agent_id: str
    human_responsible_scim_ref: str | None
    current_state: str  # from atf_state.derive


@dataclass
class ReconcileResult:
    agent_id: str
    action: ReconcileAction
    reason: str


def reconcile(
    agents: Iterable[AgentRecord],
    scim_lookup: Callable[[str], ScimStatus],
) -> list[ReconcileResult]:
    """§9.1 rule: identity_valid ∧ human_responsible_resolvable == True
    → agent may leave UNKNOWN; orphaned → QUARANTINED.

    scim_lookup(scim_ref) → 'ACTIVE' | 'SUSPENDED' | 'NOT_FOUND'
    """
    out: list[ReconcileResult] = []
    for a in agents:
        if not a.human_responsible_scim_ref:
            out.append(ReconcileResult(
                a.agent_id, "QUARANTINE",
                "no human_responsible reference set",
            ))
            continue
        try:
            status = scim_lookup(a.human_responsible_scim_ref)
        except Exception as exc:
            # SCIM outage doesn't quarantine (that would DoS every agent);
            # log the transient and skip — a Redis-backed heartbeat guards
            # the outage window in a real deployment.
            out.append(ReconcileResult(
                a.agent_id, "OK",
                f"scim_lookup_transient: {type(exc).__name__}",
            ))
            continue

        if status == "NOT_FOUND":
            out.append(ReconcileResult(
                a.agent_id, "QUARANTINE",
                f"human_responsible {a.human_responsible_scim_ref} orphaned",
            ))
        elif status == "SUSPENDED":
            out.append(ReconcileResult(
                a.agent_id, "QUARANTINE",
                f"human_responsible {a.human_responsible_scim_ref} suspended",
            ))
        elif a.current_state == "QUARANTINED":
            out.append(ReconcileResult(
                a.agent_id, "RESTORE",
                "human_responsible reappeared active",
            ))
        else:
            out.append(ReconcileResult(a.agent_id, "OK", ""))
    return out


if __name__ == "__main__":
    directory = {
        "scim://acme/Users/alice":  "ACTIVE",
        "scim://acme/Users/bob":    "SUSPENDED",
        "scim://acme/Users/carol":  "ACTIVE",
    }

    def lookup(ref: str) -> ScimStatus:
        return directory.get(ref, "NOT_FOUND")  # type: ignore[return-value]

    agents = [
        AgentRecord("ag_1", "scim://acme/Users/alice", "VERIFIED"),
        AgentRecord("ag_2", "scim://acme/Users/bob",   "VERIFIED"),
        AgentRecord("ag_3", None,                       "VERIFIED"),
        AgentRecord("ag_4", "scim://acme/Users/dave",   "VERIFIED"),   # not in dir
        AgentRecord("ag_5", "scim://acme/Users/carol", "QUARANTINED"),
    ]
    r = reconcile(agents, lookup)
    by_id = {res.agent_id: res for res in r}
    assert by_id["ag_1"].action == "OK"
    assert by_id["ag_2"].action == "QUARANTINE"
    assert by_id["ag_3"].action == "QUARANTINE"
    assert by_id["ag_4"].action == "QUARANTINE"
    assert by_id["ag_5"].action == "RESTORE"

    # SCIM outage → transient OK, not mass quarantine
    def flaky(_ref: str) -> ScimStatus:
        raise RuntimeError("scim down")
    r2 = reconcile([AgentRecord("ag_x", "scim://a/b", "VERIFIED")], flaky)
    assert r2[0].action == "OK"
    assert "transient" in r2[0].reason

    print("scim_agent OK")
