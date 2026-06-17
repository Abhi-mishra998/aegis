# Threat Graph

## What this page is for

The Threat Graph page is the operator's single-screen view of an agent's actual blast radius. Pick an agent from the topbar, and the page shows two things side by side:

- **The IAG graph** — the agent at the centre, every tool it has actually called as a solid red edge ("touched"), and every other tool in the tenant's pool that it could reach via its role bindings as a dashed amber edge ("untouched but reachable").
- **The MITRE ATT&CK coverage grid** — the platform's 36 signals laid out across 9 tactics, with the cells the selected agent has triggered rendered full colour and the rest faded with a dashed border.

It answers two questions at once: "what has this agent done?" and "what could it still reach if it were compromised tomorrow?" Touched is what already burned; untouched is the surface area the kill switch protects you against.

The page is named *Threat Graph* in the UI even though the underlying data is the Identity & Access Graph — operators kept calling it the threat graph during triage, and we kept the name.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/threat-graph`.
- **Keyboard hint**: `G T`.
- **Minimum role for read**: `AUDITOR`.
- **Re-ingest** is also `AUDITOR`+. It is a tenant-scoped write to the IAG cache; it does not change the audit log.

## What you see

- **Page header** — title, one-sentence subtitle ("Touched (solid) vs reachable-but-untouched (dashed) resources surface the blast radius your agent could have hit but didn't"), a `Refresh` button, and a `Re-ingest` button.
- **IAG graph card** (left, takes 3/5 of the row on `xl`) — three-column React Flow canvas:
  - **Column 1 (left)**: the agent root node, labelled with the first 16 characters of the agent UUID.
  - **Column 2 (middle)**: every tool in `touched_resources`, rendered in solid red with a continuous arrow from the agent.
  - **Column 3 (right)**: every tool in `untouched_resources`, rendered in dashed amber with a dashed arrow from the agent.
  - Each column caps at 24 nodes — sufficient for every production tenant we have seen.
- **IAG footer** under the graph — four counters: `Touched`, `Reachable`, `Criticality`, and `Ingest` timestamp (UTC, minute precision).
- **MITRE coverage grid** (right, takes 2/5 of the row on `xl`) — one column per tactic (TA0001 through TA0040), one cell per technique, coloured by the maximum severity of any signal in that technique.
  - **Header readout** when an agent is in scope: `2/9 tactics fired · 3/36 techniques · last 7d`.
  - **Touched cells** render at full opacity with a "• fired" badge.
  - **Untouched cells** render at 40% opacity with a dashed border.
  - Hover any cell to see the signal IDs, severities, default scores, and one-line descriptions.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Per-agent IAG view | GET | `/iag/agents/{agent_id}` | gateway (`services/gateway/routers/iag.py`) |
| MITRE coverage (per agent) | GET | `/iag/mitre-coverage?agent_id={uuid}&days=7` | gateway |
| Re-ingest IAG from audit | POST | `/iag/refresh?days={n}` | gateway |

### `GET /iag/agents/{agent_id}` response shape

```json
{
  "agent_id": "…",
  "touched_resources":   ["read_file", "sql_query"],
  "untouched_resources": ["financial.wire_transfer"],
  "by_kind":             {"data": 2, "financial": 1},
  "by_kind_dollars":     {"financial": 250000},
  "dollar_estimate":     250000,
  "criticality_score":   10,
  "system_values_configured": true,
  "last_ingest_ts":      1750000000
}
```

The `touched_resources` slice is derived per-request from the audit log — every `tool` value the agent has emitted on `action=execute_tool` in the configured window. The `untouched_resources` slice is the tenant-wide tool pool synthesised by the last `POST /iag/refresh` minus what the agent has touched. `criticality_score` is the sensitivity-weighted sum from `services/security/iag/graph.py:compute_blast_radius`.

### `GET /iag/mitre-coverage?agent_id=…&days=7` enrichment

Without `agent_id`, the response is the static signal registry (36 signals across 9 tactics). When `agent_id` is supplied, every level of the response is annotated:

- Each **signal** gains `touched: bool`.
- Each **technique** gains `touched_count` (number of touched signals).
- Each **tactic** gains `touched_techniques` and `touched_signals` counts.
- The top level gains `touched_tactics`, `touched_techniques_total`, `touched_signals_total`.

The grid header reads `2/9 tactics fired · 3/36 techniques · last 7d` directly from these counts.

### `POST /iag/refresh?days=N`

Synthesises the IAG cache from the tenant's audit log. The handler walks `/logs/search` over the last `N` days (default 30), groups rows by `(agent_id, tool)`, and writes the result as one synthetic tenant-wide role that grants every agent access to every tool any agent in the tenant has ever used. Returns a small JSON envelope:

```json
{
  "agents_seen":     5,
  "resources_synth": 3,
  "edges":           15,
  "last_ingest_ts":  1750000000
}
```

Source: `services/gateway/routers/iag.py:refresh_iag_from_audit`.

## Auto-refresh & realtime

- **No auto-refresh.** The graph and the coverage grid both load on mount and on agent change, plus when the operator clicks Refresh or changes the selected agent.
- **No SSE.** Re-ingest is operator-driven on purpose — it walks the audit log and the latency is worth surfacing through a button click rather than hiding inside a poll loop.

## Per-agent scoping

Yes. The page reads `selectedAgentId` from `AgentContext` (the shared topbar agent picker). Without a selected agent the canvas shows a "Select an agent from the topbar to load its IAG graph" placeholder; the coverage grid still renders the static registry.

## How an operator actually drives it

1. Pick an agent from the topbar picker.
2. Click **Re-ingest** — this rebuilds the tenant's IAG cache from the most recent `days=30` of audit history. Without this step the `untouched_resources` slice is whatever the last operator's ingestion left behind, which on a fresh tenant is empty.
3. The page re-fetches `/iag/agents/{id}` and the MITRE coverage. Solid red edges show what the agent has actually called; dashed amber edges show the rest of the tenant tool pool.
4. Hover MITRE cells to read the signal definitions, look at the "• fired" badges to see which techniques the agent has already exercised.
5. The counters under the graph (`Touched`, `Reachable`, `Criticality`, `Ingest` timestamp) are the report-ready summary an operator can paste into an incident ticket.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No agent selected | `Select an agent from the topbar to load its IAG graph.` | Pick an agent in the topbar picker. |
| Selected agent has no touched + no untouched resources | `No accessible resources recorded for this agent yet — run some traffic.` | Either the agent has no audit history in the window, or `POST /iag/refresh` has never been called for this tenant. Click **Re-ingest**. If the agent is genuinely silent, run one Playground call to seed an audit row. |
| Re-ingest succeeds but graph still empty | (counter footer shows `Touched: 0  Reachable: 0`) | The tenant has no `execute_tool` audit rows in the last `N` days. Extend the window via `POST /iag/refresh?days=90`. |

The empty-state copy fires whenever `touched_resources` and `untouched_resources` are both empty. The two underlying causes are distinct (no audit history vs. no ingestion has ever run) but the surface is the same — the Re-ingest button covers both.

## Edge cases & known gotchas

- **Re-ingest is tenant-scoped, not agent-scoped.** One click rebuilds the cache for every agent in the tenant. That's why the synthetic role grants tenant-wide access — the per-agent narrowing happens at read time on `GET /iag/agents/{id}` against the audit log.
- **Solid-red edges are read-time, not cache-time.** Even if `Re-ingest` has not been clicked, the touched slice is computed from the audit log per request. So `Touched` can be non-zero with `Reachable: 0` — that's the "no ingestion has run" signature.
- **Coverage grid caches the static registry.** Reloading the page is the only way to pick up a new signal — the registry is module-level in `services/security/signal_registry.py` and immutable after import.
- **Sprint 5 stale-cache bug**: before the fix that landed mid-2026-06, Re-ingest was never wired to a scheduler, so every tenant saw `agents_seen: 0` and the graph rendered the empty state forever. The current page surfaces this explicitly: the `Ingest` timestamp in the footer reads `last_ingest_ts=0` when no ingestion has ever run.
- **Tool names are intentionally long.** Truncation in the column cells caps at 28 characters with an ellipsis; hover the cell for the full tool name.
- **Per-EC2 flap**: `/iag/*` is proxied through the gateway and is stable.

## Related docs

- [Identity Graph](identity-graph.md) — the broader graph (`/graph/*`) showing every typed node and edge across the tenant. Threat Graph is the per-agent slice; Identity Graph is the tenant-wide view.
- [Identity Graph service](../../services/identity-graph.md)
- [Threat Scenarios](../../security/threat-scenarios.md) — what a compromised agent looks like in this view.
- [Forensics](forensics.md) — the page operators usually open next after spotting a high-criticality agent here.
- [Detection Pipeline](../../security/detection-pipeline.md) — the source of the 36-signal MITRE coverage grid.

## URL

`https://aegisagent.in/threat-graph`

## Screenshot

![Threat Graph](../_screenshots/threat-graph.png)
