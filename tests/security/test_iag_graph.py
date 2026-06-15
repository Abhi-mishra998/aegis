"""Sprint 5 — IAG blast-radius pure-function tests.

All tests target `compute_blast_radius()` against synthetic graphs.
No Redis, no DB, no I/O.
"""
from __future__ import annotations

from services.security.iag.graph import (
    KIND_TABLE,
    KIND_VAULT,
    ResourceMeta,
    SENS_CRITICAL,
    SENS_HIGH,
    SENS_LOW,
    SENS_MEDIUM,
    _SENSITIVITY_WEIGHT,
    compute_blast_radius,
)


def _meta(rid: str, sens: str, kind: str = KIND_TABLE, label: str | None = None) -> ResourceMeta:
    return ResourceMeta(resource_id=rid, kind=kind, label=label or rid, sensitivity=sens)


def test_blast_radius_empty_agent_no_roles_no_access():
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles=set(),
        role_perms={},
        perm_resources={},
        resource_meta={},
    )
    assert br.accessible_resources == []
    assert br.untouched_resources == []
    assert br.criticality_score == 0
    assert br.by_kind == {}


def test_blast_radius_single_agent_single_role_single_resource():
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles={"r_dba"},
        role_perms={"r_dba": {"p_select_customers"}},
        perm_resources={"p_select_customers": {"customers"}},
        resource_meta={"customers": _meta("customers", SENS_HIGH)},
    )
    assert br.accessible_resources == ["customers"]
    assert br.untouched_resources == ["customers"]
    assert br.criticality_score == _SENSITIVITY_WEIGHT[SENS_HIGH]
    assert br.by_kind == {KIND_TABLE: 1}


def test_blast_radius_touched_subtracted_from_untouched():
    """Resource appears in touched → drops out of `untouched` and the score."""
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources={"customers"},
        agent_roles={"r_dba"},
        role_perms={"r_dba": {"p1", "p2"}},
        perm_resources={"p1": {"customers"}, "p2": {"orders"}},
        resource_meta={
            "customers": _meta("customers", SENS_CRITICAL),
            "orders":    _meta("orders",    SENS_MEDIUM),
        },
    )
    assert br.touched_resources == ["customers"]
    assert br.untouched_resources == ["orders"]
    # Critical (touched) excluded from score; only medium (untouched) counts.
    assert br.criticality_score == _SENSITIVITY_WEIGHT[SENS_MEDIUM]


def test_blast_radius_dedups_resource_granted_by_two_roles():
    """A resource reachable through two distinct roles counts once."""
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles={"r1", "r2"},
        role_perms={"r1": {"p1"}, "r2": {"p2"}},
        perm_resources={"p1": {"shared_table"}, "p2": {"shared_table"}},
        resource_meta={"shared_table": _meta("shared_table", SENS_LOW)},
    )
    assert br.accessible_resources == ["shared_table"]
    # Score is 1 (low weight), NOT 2 — dedup happened.
    assert br.criticality_score == _SENSITIVITY_WEIGHT[SENS_LOW]


def test_blast_radius_by_kind_groups_correctly():
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles={"r_admin"},
        role_perms={"r_admin": {"p_all"}},
        perm_resources={"p_all": {"t1", "t2", "v1"}},
        resource_meta={
            "t1": _meta("t1", SENS_HIGH, kind=KIND_TABLE),
            "t2": _meta("t2", SENS_HIGH, kind=KIND_TABLE),
            "v1": _meta("v1", SENS_CRITICAL, kind=KIND_VAULT),
        },
    )
    assert br.by_kind == {KIND_TABLE: 2, KIND_VAULT: 1}
    assert br.criticality_score == (
        2 * _SENSITIVITY_WEIGHT[SENS_HIGH] + _SENSITIVITY_WEIGHT[SENS_CRITICAL]
    )


def test_blast_radius_unknown_resource_meta_scores_zero():
    """A resource without a meta entry contributes nothing to the score."""
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles={"r1"},
        role_perms={"r1": {"p1"}},
        perm_resources={"p1": {"mystery_resource"}},
        resource_meta={},
    )
    assert br.untouched_resources == ["mystery_resource"]
    assert br.criticality_score == 0
    # Also, no kind buckets — we won't invent a kind.
    assert br.by_kind == {}


def test_blast_radius_output_is_sorted_for_diff_friendliness():
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources={"z_touched"},
        agent_roles={"r1"},
        role_perms={"r1": {"p1"}},
        perm_resources={"p1": {"z_touched", "a_untouched", "m_untouched"}},
        resource_meta={
            "z_touched":   _meta("z_touched",   SENS_HIGH),
            "a_untouched": _meta("a_untouched", SENS_HIGH),
            "m_untouched": _meta("m_untouched", SENS_HIGH),
        },
    )
    # Sorted alphabetically — easy to diff between runs.
    assert br.accessible_resources == ["a_untouched", "m_untouched", "z_touched"]
    assert br.untouched_resources == ["a_untouched", "m_untouched"]


def test_blast_radius_labels_only_include_surfaced_resources():
    """A 50k-resource tenant shouldn't get a 50k-key labels dict back —
    we only surface labels for the resources actually returned."""
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources={"touched_t"},
        agent_roles={"r1"},
        role_perms={"r1": {"p1"}},
        perm_resources={"p1": {"touched_t", "untouched_t"}},
        resource_meta={
            "touched_t":     _meta("touched_t",     SENS_HIGH, label="customers PII"),
            "untouched_t":   _meta("untouched_t",   SENS_MEDIUM, label="orders ledger"),
            "unrelated":     _meta("unrelated",     SENS_LOW, label="dev sandbox"),
        },
    )
    assert "unrelated" not in br.resource_labels
    assert br.resource_labels["touched_t"] == "customers PII"
    assert br.resource_labels["untouched_t"] == "orders ledger"


def test_blast_radius_dict_roundtrip_is_json_safe():
    """to_dict() returns a structure suitable for json.dumps without
    special encoders — the API contract."""
    import json
    br = compute_blast_radius(
        agent_id="agA", incident_id="INC-1",
        touched_resources=set(),
        agent_roles={"r1"},
        role_perms={"r1": {"p1"}},
        perm_resources={"p1": {"t1"}},
        resource_meta={"t1": _meta("t1", SENS_HIGH)},
    )
    s = json.dumps(br.to_dict())
    parsed = json.loads(s)
    assert parsed["agent_id"] == "agA"
    assert parsed["criticality_score"] == _SENSITIVITY_WEIGHT[SENS_HIGH]
