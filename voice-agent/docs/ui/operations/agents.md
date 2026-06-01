# Agents

## What this page is for

The Agents page is the operator's home for the per-tenant agent registry. It answers two day-to-day questions: "which agents do we have running?" and "what is each one allowed to do?" From this page an operator creates new agents, quarantines a misbehaving one in a single click, reactivates after triage, and clicks through to a per-agent profile for the deep dive.

A second page — Agent Profile — is reached by clicking any agent row. It shows the agent's tool-usage breakdown, risk trend, drift score versus its 7-day baseline, peer benchmark, and the last 15 audit entries.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Paths**: `/agents` (list), `/agents/{id}/profile` (profile).
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`. `VIEWER` can also list and view profiles.
- **Create, update, delete, quarantine, reactivate** require `ADMIN` or `SECURITY`. A `VIEWER` opening the page sees the rows but every write button returns the platform's 403 explaining the role requirement.

## What you see

### `/agents` list

- **KPI tiles** — Total agents, Active, Quarantined, Average risk level. Computed from `/agents/summary`.
- **Search box** — local filter by agent name.
- **Agents table** — name, owner, status badge, risk_level chip, permission count, last-active timestamp, action menu.
- **"New Agent" button** — top right. Opens a modal with name, description, and risk_level fields.
- **Action menu per row** — Quarantine / Reactivate / Delete.

### `/agents/{id}/profile`

- **Header** — agent name, status, risk_level, owner email.
- **Tool usage** — bar chart of per-tool call counts in the window.
- **Risk trend** — line chart of mean risk score over time.
- **Drift score** — single number with a sparkline; this agent's deviation from its 7-day baseline.
- **Peer benchmark** — where this agent ranks against other agents of the same risk_level.
- **Recent audit entries** — last 15 rows linking to Audit Trail.
- **Findings breakdown** — pie of the agent's recent findings.

## Backend calls

### `/agents` list

| Action | HTTP | API path | Service |
|---|---|---|---|
| List agents | GET | `/agents` | registry |
| KPI summary | GET | `/agents/summary` | registry |
| Create agent | POST | `/agents` | registry |
| Update agent (status) | PATCH | `/agents/{id}` | registry |
| Delete agent (soft) | DELETE | `/agents/{id}` | registry |

### Agent Profile

| Action | HTTP | API path | Service |
|---|---|---|---|
| Get agent | GET | `/agents/{id}` | registry |
| Get profile aggregate | GET | `/agents/{id}/profile` | registry |
| Recent logs | GET | `/audit/logs?agent_id={id}&limit=15` | audit |
| Drift report | GET | `/audit/drift/{id}` | audit |
| Risk trend | GET | `/audit/risk-trend/{id}` | audit |
| Peer benchmark | GET | `/audit/peer-benchmark/{id}` | audit |
| Tool usage | GET | `/audit/tool-usage/{id}` | audit |
| Daily decisions | GET | `/audit/agent-daily-decisions/{id}` | audit |
| Findings breakdown | GET | `/audit/agent-findings/{id}` | audit |

## Auto-refresh & realtime

- **List refresh**: every 30 seconds via `setInterval(fetchAgents, 30_000)` at `ui/src/pages/Agents.jsx:107`.
- **Profile**: no auto-refresh. The profile loads once on mount and on agent change.
- **No SSE.**

## Per-agent scoping

The list page is tenant-scoped, not agent-scoped — it shows every agent. The Profile page is agent-scoped via the URL path. Clicking a row in the list pushes the agent into the sidebar `useAgents` picker via `setSelectedAgentId(agent.id)` so downstream pages stay focused.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No agents at all | `No agents registered yet.` | Click "New Agent" to register the first one. |
| Search has no matches | `No agents match "<query>".` | Clear the search. |
| Profile — no recent findings | `No findings recorded in this window` | Expected for quiet agents. |
| Profile — no decision data | `No decision data in window` | Extend the window or trigger one Playground call. |
| Profile — no tool activity | `No tool activity recorded in this window.` | Same as above. |
| Profile — no trend data | `No trend data available.` | Same as above. |

## Edge cases & known gotchas

- **`/agents` list returns 500**: legacy NULL `metadata` rows are coerced to `{}` by the `AgentResponse` field validator. If 500s appear after a deploy, verify the validator fix is shipped.
- **Quarantine takes effect immediately, but the permission cache lags**: agent permission lookups in the gateway cache for 60 seconds. Stage 4 may continue to honor a quarantined agent's grants for up to 60 seconds; the kill switch is the fast path when seconds matter.
- **Delete is soft**: the row stays, `deleted_at` is set. Listing filters on `deleted_at IS NULL`. To bring an agent back, an operator would need a backend insert; UI does not restore.
- **Profile loads slowly**: 9 backend calls are issued in parallel via `Promise.allSettled`. A slow audit aggregator hurts overall load time more than the registry calls do.
- **Per-EC2 flap**: the gateway proxies `/agents/*` as a catch-all under `proxy_agents` in `services/gateway/main.py`; this path is stable across EC2s after the deploy-topology fix.

## Related docs

- [Registry service](../../services/registry.md)
- [Audit service](../../services/audit.md) — aggregator endpoints feeding the profile
- [Behavior service](../../services/behavior.md) — source of the drift score
- [RBAC UI](../settings/rbac.md) — for editing per-agent permissions

## Screenshot

![Agents](../_screenshots/agents.png)
