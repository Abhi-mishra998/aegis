"""ATF v3.2 §Phase 3 item 2 — Per-tenant interaction graph collusion detection.

Two primitives:

  * `label_propagation_communities` — Raghavan et al. (2007) label-
    propagation community detection. Each node adopts the community
    of its heaviest-weighted neighbour majority; iterated to stability.
    Cheap (linear time per pass), deterministic given input node order,
    and sufficient for the tenant-scale graphs (<10k nodes) that the
    audit envisions. Louvain modularity-optimisation is the roadmap
    upgrade path when a tenant crosses that scale.
  * `taint_propagate` — given a seed set of "known-bad" agents (a
    CONTRADICTED verdict, an attempted C3 policy violation, etc.),
    return every agent reachable within N hops weighted by edge
    strength. The reachable set becomes a QUARANTINE candidate.

No NetworkX, no NumPy. Graph shape: `{node: [(neighbor, weight), ...]}`.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

# ponytail: label propagation is O(V+E) per pass, converges in a handful
# of passes on this scale, and matches the demonstration property the
# audit expects (tightly connected agents cluster). Upgrade to Blondel
# Louvain when the tenant crosses ~10k agents.


Graph = dict[str, list[tuple[str, float]]]


def label_propagation_communities(
    g: Graph, *, max_passes: int = 20, min_edge_weight: float = 0.0,
) -> dict[str, int]:
    """Return a `{node: community_id}` dict — label propagation communities.

    Deterministic given the input node order (relies on dict insertion
    order, which is stable in Python 3.7+). Same graph → same partition.

    ``min_edge_weight`` filters weak / incidental interactions from the
    propagation. Collusion is repeated coordination — a single log-read
    (weight 0.1) is not evidence and should not drag its neighbour's
    community label across a weakly-linked node. The default 0.0 means
    "treat every edge as evidence"; callers pass a real threshold when
    they want to isolate weakly connected nodes.
    """
    if not g:
        return {}

    labels: dict[str, int] = {n: i for i, n in enumerate(g.keys())}

    for _ in range(max_passes):
        changed = False
        for node in g.keys():
            weights_to: dict[int, float] = defaultdict(float)
            for nbr, w in g.get(node, []):
                if nbr == node or float(w) < min_edge_weight:
                    continue
                weights_to[labels[nbr]] += float(w)
            if not weights_to:
                continue
            # Pick the label with the highest incoming weight; ties broken
            # deterministically by lower label id so the pass is idempotent.
            best_label = min(
                weights_to.keys(),
                key=lambda lbl: (-weights_to[lbl], lbl),
            )
            if best_label != labels[node]:
                labels[node] = best_label
                changed = True
        if not changed:
            break

    # Compact label IDs to a dense 0..N-1 range for downstream consumers.
    dense: dict[int, int] = {}
    for n, c in labels.items():
        if c not in dense:
            dense[c] = len(dense)
        labels[n] = dense[c]
    return labels


def taint_propagate(
    g: Graph,
    seeds: Iterable[str],
    *,
    max_hops: int = 2,
    min_edge_weight: float = 0.0,
) -> set[str]:
    """BFS from the seed set, bounded by hop count and edge weight.

    Weight gate lets the caller ignore incidental interactions (a single
    log-read) while catching coordination (repeated task hand-offs).
    """
    if max_hops < 0:
        raise ValueError("max_hops must be ≥ 0")
    reachable: set[str] = set(seeds)
    frontier: set[str] = set(reachable)
    for _ in range(max_hops):
        next_frontier: set[str] = set()
        for node in frontier:
            for nbr, w in g.get(node, []):
                if float(w) < min_edge_weight or nbr in reachable:
                    continue
                next_frontier.add(nbr)
                reachable.add(nbr)
        if not next_frontier:
            break
        frontier = next_frontier
    return reachable


if __name__ == "__main__":
    # 3 agents heavily interact, 1 agent barely connects.
    g = {
        "a": [("b", 5.0), ("c", 4.0), ("outsider", 0.1)],
        "b": [("a", 5.0), ("c", 5.0)],
        "c": [("a", 4.0), ("b", 5.0)],
        "outsider": [("a", 0.1)],
    }

    # min_edge_weight=1.0 filters the incidental 0.1 tie, keeping outsider
    # in its own cluster instead of being absorbed by the a/b/c majority.
    communities = label_propagation_communities(g, min_edge_weight=1.0)
    assert communities["a"] == communities["b"] == communities["c"], communities
    assert communities["outsider"] != communities["a"], communities

    # Taint: seed with a CONTRADICTED agent; the cluster gets tainted, outsider does not (below threshold).
    tainted = taint_propagate(g, seeds={"a"}, max_hops=1, min_edge_weight=1.0)
    assert "b" in tainted and "c" in tainted
    assert "outsider" not in tainted

    # With hop=0, only the seed is returned
    just_seed = taint_propagate(g, seeds={"a"}, max_hops=0)
    assert just_seed == {"a"}

    # Empty graph is safe
    assert label_propagation_communities({}) == {}
    assert taint_propagate({}, seeds={"a"}) == {"a"}

    # Larger cluster: 2 communities of 4 nodes each, weakly linked
    g2: Graph = {}
    left = ["l1", "l2", "l3", "l4"]
    right = ["r1", "r2", "r3", "r4"]
    for a in left:
        g2[a] = [(b, 5.0) for b in left if b != a] + [("r1", 0.1)]
    for a in right:
        g2[a] = [(b, 5.0) for b in right if b != a] + [("l1", 0.1)]
    c2 = label_propagation_communities(g2, min_edge_weight=1.0)
    left_comm = {c2[n] for n in left}
    right_comm = {c2[n] for n in right}
    # Each side collapses to one community, and they're distinct.
    assert len(left_comm) == 1 and len(right_comm) == 1, (left_comm, right_comm)
    assert left_comm.isdisjoint(right_comm)

    print("collusion OK")
