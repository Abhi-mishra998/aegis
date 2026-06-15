"""Sprint 5 — Identity & Access Graph reasoning (pure-Python).

Given a sparse graph of (agent → role → permission → resource) edges and the
set of resources an agent *actually touched* during an incident, compute the
**blast radius** — the resources the agent could have reached but didn't.

Design rules:
  1. Pure function. No I/O. No Redis. No DB. Easy to unit-test.
  2. Deterministic. Same input → same output. No clock, no random.
  3. Resource sensitivity is data, not policy — kept on `ResourceMeta` so
     the scoring function can be swapped without touching graph traversal.
  4. The output is the contract `/iag/incidents/{id}/blast-radius` returns;
     additive changes (new fields) are forward-compatible.

This module knows nothing about how the graph is sourced (Postgres,
IAM, Vault) — it only knows how to *reason* about it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Resource taxonomy. Sensitivities are an ordered enum so they round-trip
# cleanly through JSON. The numeric weight feeds the `criticality_score`
# without locking us into integer-ordered enum semantics.
# ---------------------------------------------------------------------------
SENS_CRITICAL = "critical"   # PII tables, payment APIs, vault secrets
SENS_HIGH     = "high"       # internal customer data, prod DBs
SENS_MEDIUM   = "medium"     # internal app data, staging
SENS_LOW      = "low"        # dev sandboxes, public APIs

_SENSITIVITY_WEIGHT = {
    SENS_CRITICAL: 25,
    SENS_HIGH:     10,
    SENS_MEDIUM:   3,
    SENS_LOW:      1,
}

# Resource kinds we model. Adapters tag every edge with one.
KIND_TABLE     = "table"        # Postgres / Snowflake / BigQuery table
KIND_S3_PREFIX = "s3_prefix"
KIND_K8S_NS    = "k8s_namespace"
KIND_VAULT     = "vault_path"
KIND_HTTP_DEST = "http_destination"
KIND_QUEUE     = "queue"


@dataclass(frozen=True)
class ResourceMeta:
    """Static information about one resource node in the IAG."""
    resource_id:   str
    kind:          str        # one of KIND_* constants
    label:         str        # human-readable name; logged into reports
    sensitivity:   str        # one of SENS_* constants

    def weight(self) -> int:
        return _SENSITIVITY_WEIGHT.get(self.sensitivity, 0)


@dataclass
class BlastRadius:
    """What an agent *could* have reached vs. what it actually touched.

    Surfaced into the storyline JSON as the `blast_radius` field so the SOC
    sees the counterfactual: "If we hadn't blocked at step 4, the agent
    still had access to N more sensitive resources."
    """
    agent_id:             str
    incident_id:          str
    accessible_resources: list[str]          # every resource the agent could reach
    touched_resources:    list[str]          # resources the incident actually touched
    untouched_resources:  list[str]          # accessible \ touched
    criticality_score:    int                # sum of ResourceMeta.weight() over untouched
    by_kind:              dict[str, int]     # counts of untouched resources per kind
    resource_labels:      dict[str, str]     # resource_id → label, only for the ones surfaced

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Pure traversal
# ---------------------------------------------------------------------------

def _resources_for_agent(
    *,
    agent_roles:    set[str],
    role_perms:     dict[str, set[str]],
    perm_resources: dict[str, set[str]],
) -> set[str]:
    """Forward walk: agent → role → permission → resource.

    Output is the *set* of resource_ids reachable from any role this agent
    holds. Duplicates collapse — a resource granted by two roles still
    counts once toward the blast radius.
    """
    reached: set[str] = set()
    for role_id in agent_roles:
        for perm_id in role_perms.get(role_id, ()):
            reached |= perm_resources.get(perm_id, set())
    return reached


def compute_blast_radius(
    *,
    agent_id:           str,
    incident_id:        str,
    touched_resources:  Iterable[str],
    agent_roles:        Iterable[str],
    role_perms:         dict[str, set[str]],
    perm_resources:     dict[str, set[str]],
    resource_meta:      dict[str, ResourceMeta],
) -> BlastRadius:
    """Compute the BlastRadius dataclass for one (agent, incident) pair.

    `touched_resources` is the set of resource_ids that appeared in the
    Storyline's steps (Sprint 4). Anything reachable but not touched is
    counted as prevented. The weight comes from `resource_meta`; resources
    that don't have a meta entry score zero (we won't invent sensitivity).

    Resource labels are returned only for the resources we surface — for a
    tenant with 50k resources we don't want a 50k-key map in every JSON
    response. The SOC UI can look up missing labels via a follow-up
    `/iag/resources/{id}` query if it ever needs to.
    """
    touched_set = {r for r in touched_resources if r}
    accessible = _resources_for_agent(
        agent_roles=set(agent_roles),
        role_perms=role_perms,
        perm_resources=perm_resources,
    )
    # Keep deterministic ordering for the JSON output so callers can diff
    # snapshots; sorted() on strings is the natural choice.
    accessible_sorted = sorted(accessible)
    touched_sorted    = sorted(touched_set)
    untouched_set     = accessible - touched_set
    untouched_sorted  = sorted(untouched_set)

    by_kind: dict[str, int] = {}
    score = 0
    labels: dict[str, str] = {}
    for rid in untouched_sorted:
        meta = resource_meta.get(rid)
        if not meta:
            continue
        by_kind[meta.kind] = by_kind.get(meta.kind, 0) + 1
        score += meta.weight()
        labels[rid] = meta.label
    # Also include touched-resource labels so the response is self-contained.
    for rid in touched_sorted:
        meta = resource_meta.get(rid)
        if meta:
            labels.setdefault(rid, meta.label)

    return BlastRadius(
        agent_id=agent_id,
        incident_id=incident_id,
        accessible_resources=accessible_sorted,
        touched_resources=touched_sorted,
        untouched_resources=untouched_sorted,
        criticality_score=score,
        by_kind=by_kind,
        resource_labels=labels,
    )
