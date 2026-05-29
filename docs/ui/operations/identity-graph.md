# Identity Graph

## What this page is for

The Identity Graph page visualizes every typed node — `agent`, `tool`, `resource`, `tenant`, `human` — connected by typed directed edges — `invokes`, `reads`, `writes`, `delegates`, `escalates`. It's the page an operator opens during incident triage when they want to answer "if this agent is compromised, what else is in scope" and during quarterly reviews when they want to see the actual call topology of the platform.

The page has two interactive modes: a static blast-radius query (one node, one depth, what's reachable) and a what-if compromise simulation (one node, one scenario, what an attacker could do).

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/identity-graph`.
- **Keyboard hint**: `G G`.
- **Minimum role for read**: `AUDITOR`.
- **Compromise simulation** is also a read (`POST /graph/compromise/simulate` is computationally a query, not a mutation) and works for `AUDITOR`+. Writing nodes or edges manually requires `ADMIN` or `SECURITY` but that is done from the API, not the UI.

## What you see

- **Force-directed graph canvas** — the main panel. Nodes are colored by type, sized by trust score. Edges are colored by outcome (allow / deny / error) and weighted by risk_score.
- **Node click** — opens a side panel with the node's name, type, trust score, drift score, and attributes.
- **"Blast Radius" button** — in the side panel; runs `/graph/blast-radius/{node.id}` at the current depth slider value and overlays the reachable set on the graph.
- **Depth slider** — top right, 1 to 6.
- **Scenario picker** — `stolen_token` / `insider_threat` / `delegation_chain_abuse`.
- **"Run compromise sim" button** — fires `POST /graph/compromise/simulate` and shows the affected_tenants, blast_radius count, risk_score, and reachable_nodes summary.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load full graph (nodes + edges) | GET | `/graph/agents?limit=500` | identity_graph |
| Blast radius | GET | `/graph/blast-radius/{node_id}?depth={n}` | identity_graph |
| Compromise simulation | POST | `/graph/compromise/simulate` | identity_graph |

## Auto-refresh & realtime

- **Graph refresh**: every 30 seconds via `setInterval(fetchAll, 30_000)` at `ui/src/pages/IdentityGraph.jsx:98`.
- **No SSE.** Edges accumulate continuously; the graph reflects the latest snapshot at the last poll.

## Per-agent scoping

No. The graph spans the whole tenant — every node in the tenant is rendered. Filtering by agent would defeat the purpose ("show me what this agent can reach" is the blast-radius query, not a list filter).

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No nodes for the tenant | "No agents in graph" | Seed nodes via `POST /graph/nodes` or follow the `demos/*/setup_demo.py` flow. |
| Blast radius — actor has no edges | `No reachable nodes recorded.` | Expected for newly-created nodes. |

The public production demo has 10 nodes and 13 edges at the time of writing — small enough that every node fits on the canvas without zooming. Larger tenants will need pan/zoom (already implemented).

## Edge cases & known gotchas

- **`/graph/agent/{id}` returns 404**: caller passed the registry agent_id rather than the graph node_id. The graph_nodes UUID is different from the registry agents UUID. Click the node in the visualization — the UI uses `node.id`, not `external_id`.
- **Blast radius returns "Actor node not found"**: same root cause.
- **Trust score depressed for one agent**: expected when the agent has any deny edges with high risk. The current production graph has `devops-agent.trust_score = 0.49` because of one denied `k8s.delete.namespace` edge at risk 1.0.
- **Slash in `external_id`**: avoid `/` in node external_ids (use `.` instead — e.g. `crm.tickets` not `crm/tickets`). URL-encoded slashes have bitten node creation in the past.
- **Sim takes 5+ seconds**: depth > 4 across many edges. Cap depth at 3–4 for interactive use; deeper sims should run via the API and the result viewed later.
- **Per-EC2 flap**: the gateway proxies `/graph/*` via `proxy_graph` — stable.

## Related docs

- [Identity Graph service](../../services/identity-graph.md)
- [Forensics UI](forensics.md) — the consumer of blast-radius for investigations
- [Threat Scenarios](../../security/threat-scenarios.md) — what a compromised node looks like in this view
- [Gateway](../../services/gateway.md) — the upstream that emits edges after every `/execute`

## Screenshot

![Identity Graph](../_screenshots/identity-graph.png)
