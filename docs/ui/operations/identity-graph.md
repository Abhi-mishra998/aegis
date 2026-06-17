# Identity Graph

## What this page is for

The Identity Graph page visualises every typed node — `agent`, `tool`, `resource`, `tenant`, `human` — connected by typed directed edges (`invokes`, `reads`, `writes`, `delegates`, `escalates`) for the whole tenant. It is the page an operator opens during incident triage when the question is "if this agent is compromised, what else is in scope?" and during quarterly reviews when the question is "what is the actual call topology of the platform?"

The page is paired with — but distinct from — the per-agent **[Threat Graph](threat-graph.md)** at `/threat-graph`. Threat Graph is one agent vs. the tenant tool pool with a MITRE overlay. Identity Graph is every node and every edge in the tenant, with trust scoring, runtime relationship counts, trust-boundary aggregates, drift signals, and a what-if compromise simulator.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/identity-graph`.
- **Keyboard hint**: `G G`.
- **Minimum role for read**: `AUDITOR`.
- **Compromise simulation** (`POST /graph/compromise/simulate`) is computationally a query, not a mutation — `AUDITOR`+ can run it from the UI. Writing nodes or edges manually is `ADMIN`/`SECURITY` and only via the API.

## What you see

- **Force-directed graph canvas** — main panel. Nodes are coloured by `node_type`, ringed by `trust_score` (red → orange → yellow → green), and sized larger when selected. Edges are coloured by `outcome` (red `deny`, orange `error`, dark grey `allow`) and weighted by `risk_score`.
- **Live header readout** — `<n> nodes · <m> edges` so the operator can spot ingestion gaps at a glance.
- **Selected node panel** — top-right. Reads `node.name`, `node.node_type`, `node.trust_score`, `node.drift_score`. Click any node in the canvas to populate.
- **Compromise simulation panel** — middle-right. Six scenarios (`stolen_token`, `rogue_agent`, `prompt_injection`, `malicious_tool`, `lateral_movement`, `runaway_autonomy`), depth selector (1–6), and a Run button. Result opens in a centred modal — KPIs, reachable-node list, risk classification (LOW/MEDIUM/HIGH/CRITICAL).
- **Blast-radius card** — bottom-right. Auto-populates when a node is clicked: `reachable`, `affected resources`, `risk` score.

The runtime, trust-boundary, and drift surfaces below feed the underlying graph, but they are also reachable as standalone reads through the API for SIEM-style polling.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load full graph (nodes + edges) | GET | `/graph/agents?limit=500` | identity_graph |
| Single node + neighbours | GET | `/graph/agent/{node_id}` | identity_graph |
| Blast radius (bounded BFS) | GET | `/graph/blast-radius/{node_id}?depth={n}` | identity_graph |
| Top-N high-risk edges | GET | `/graph/risky-paths?limit={n}` | identity_graph |
| Trust-boundary aggregate | GET | `/graph/trust-boundaries` | identity_graph |
| Recent edges (live relationships) | GET | `/graph/runtime-relationships?minutes={n}` | identity_graph |
| Per-node trust history | GET | `/graph/trust/{node_id}?limit={n}` | identity_graph |
| Drift signals | GET | `/graph/drift?minutes={n}` | identity_graph |
| Compromise simulation | POST | `/graph/compromise/simulate` | identity_graph |

The UI client wraps these as `graphService` in `ui/src/services/api.js`. The page itself calls `listAgents` on mount and on every 30-second refresh; `getBlastRadius` on node click; `simulateCompromise` from the panel button. The remaining four endpoints (`risky-paths`, `trust-boundaries`, `runtime-relationships`, `drift`) are wired in `graphService` for SIEM forwarders and the Security Dashboard — they aren't surfaced as direct visualisations on this page yet.

### Runtime relationships

`GET /graph/runtime-relationships?minutes=60` returns up to 500 recent `GraphEdge` rows in `occurred_at` descending order. It is the "what's happening right now" feed — the gateway emits one edge per `/execute` after the decision lands, so this list captures the last hour of every tool call.

### Trust boundaries

`GET /graph/trust-boundaries` returns the tenant-level aggregate:

```json
{
  "tenant_id": "…",
  "tenant_trust_score": 0.72,
  "by_node_type": {
    "agent":    {"count": 5, "avg_trust": 0.81},
    "tool":     {"count": 12, "avg_trust": 0.67},
    "resource": {"count": 3, "avg_trust": 0.58}
  }
}
```

`tenant_trust_score` comes from `services/identity_graph/trust_engine.py:compute_tenant_trust`. It is the tenant-wide score that the Security Dashboard surfaces; the per-type breakdown is the rollup that the trust-boundary section of the dashboard reads.

### Drift signals

`GET /graph/drift?minutes=1440` returns up to 200 drift signals — per-node observations of "this node is behaving differently from its 7-day baseline." Drift requires multi-day baselines, so on a freshly-deployed tenant this endpoint returns an empty list. That is not a bug — it takes seven calendar days for the baseline learner to settle. The Drift section of the Security Dashboard surfaces the same data; see [Behavior service](../../services/behavior.md) for the baseline window.

## Auto-refresh & realtime

- **Graph refresh**: every 30 seconds via `setInterval(fetchAll, 30_000)` at `ui/src/pages/IdentityGraph.jsx:98`.
- **No SSE.** Edges accumulate continuously; the canvas reflects the latest snapshot at the last poll.

## Per-agent scoping

No. The graph spans the whole tenant by design — every node in the tenant is rendered. Filtering by agent would defeat the purpose ("show me what this agent can reach" is the blast-radius query on a clicked node, not a list filter). For the per-agent view see the [Threat Graph](threat-graph.md) page.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No nodes for the tenant | (empty canvas) | Seed nodes via `POST /graph/nodes` or follow the `demos/*/setup_demo.py` flow. The first `/execute` from any agent emits at least one edge, which auto-creates the agent and target nodes. |
| Selected node has no neighbours | `No reachable nodes recorded.` (Compromise modal reachable-nodes section) | Expected for newly-created nodes that have no edges yet. Run one `/execute` from Playground to seed an edge. |
| `/graph/drift` returns `[]` | (drift surface in downstream readers shows zero rows) | Expected on tenants younger than 7 days. The baseline learner needs the full window before it can emit drift signals. |

The Threat Graph page's empty-state copy is `No accessible resources recorded for this agent yet — run some traffic.` It fires whenever an agent has no audit history in the configured window or `POST /iag/refresh` has never been called for the tenant. Identity Graph does not use that copy — its empty surface is just a sparser canvas — but operators frequently arrive here from Threat Graph after seeing it, so it is worth knowing where the two empty states converge.

The public production demo at `https://aegisagent.in/identity-graph` ships with 10 nodes and 13 edges — small enough that every node fits on the canvas without pan/zoom. Larger tenants will need the pan/zoom controls (already implemented on the SVG canvas).

## Edge cases & known gotchas

- **`/graph/agent/{id}` returns 404**: the caller passed the registry `agent_id` rather than the graph `node_id`. The `graph_nodes` UUID is different from the registry `agents` UUID. Click the node in the canvas — the UI uses `node.id`, not `external_id`.
- **Blast radius returns "Actor node not found"**: same root cause. Click the node, do not type the UUID by hand.
- **Trust score depressed for one agent**: expected when the agent has any deny edges with high risk. The current production graph has `devops-agent.trust_score ≈ 0.49` because of one denied `k8s.delete.namespace` edge at risk 1.0.
- **`/graph/drift` empty on day one**: the baseline learner is a 7-day window. Newly-deployed tenants see an empty drift list until day eight. See [Behavior service](../../services/behavior.md) for the baseline schedule.
- **`/graph/runtime-relationships` capped at 500 rows**: the default window is 60 minutes, max 2880 minutes (48 h). For longer windows use the audit log directly via `/audit/logs/search`.
- **Slash in `external_id`**: avoid `/` in node external_ids (use `.` instead, e.g. `crm.tickets` not `crm/tickets`). URL-encoded slashes have bitten node creation in the past.
- **Sim takes 5+ seconds**: depth > 4 across many edges. Cap depth at 3–4 for interactive use; deeper sims should run via the API and the result viewed later.
- **Trust score floor on CRITICAL nodes**: any reachable node with `attributes.critical = true` floors the blast-radius `risk_score` at 0.4 (MEDIUM). This is intentional — the classification is never misleadingly LOW when production assets are in scope.
- **Per-EC2 flap**: the gateway proxies `/graph/*` via `proxy_graph` — stable.

## Related docs

- [Threat Graph](threat-graph.md) — the per-agent companion view with the IAG-from-audit + MITRE coverage overlay.
- [Identity Graph service](../../services/identity-graph.md) — schema, repository, trust engine.
- [Forensics](forensics.md) — the consumer of `blast-radius` for investigations.
- [Threat Scenarios](../../security/threat-scenarios.md) — what a compromised node looks like across the six simulation scenarios.
- [Gateway](../../services/gateway.md) — the upstream that emits edges after every `/execute`.
- [Behavior service](../../services/behavior.md) — owner of the drift score and the 7-day baseline window.

## URL

`https://aegisagent.in/identity-graph`

## Screenshot

![Identity Graph](../_screenshots/identity-graph.png)
