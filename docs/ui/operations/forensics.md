# Forensics

## What this page is for

Forensics is the analyst's investigation workbench. It surfaces high-risk events from the audit chain, walks the per-execution timeline, runs blast-radius queries, replays the agent's recent activity, and (when needed) exports a signed PDF for legal hold or external evidence.

The page is the right tool when an alert has fired and the analyst needs to answer "what happened, what was the scope, who else was reached, and can I take an artifact of this investigation off-platform."

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/forensics`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`. Every list, detail, replay, and blast-radius call is a GET.
- **PDF export** is also `AUDITOR`+; the export operation produces an audit row recording who pulled the evidence.

## What you see

- **Investigation list** — left column. Default filter `min_risk >= 0.5`, limit 20. Each row shows the central audit row's timestamp, agent, tool, risk score, and primary finding.
- **Investigation detail** — center column. Shows the agent's recent context: tool diversity, deny ratio, peer benchmark, and a window of related audit rows.
- **Timeline panel** — bottom of the center column. The Flight Recorder per-stage view for the selected audit row.
- **Blast radius panel** — right column. Reachability for the agent from the Identity Graph; toggle depth from 1 to 6.
- **Replay panel** — right column below blast radius. Last 50 audit rows for the agent, ordered by timestamp.
- **"Export PDF" button** — top right of the detail panel. Queues a render job and provides a 24-hour signed S3 URL when ready.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List investigations | GET | `/forensics/investigation?min_risk=0.5&limit=20` | forensics |
| Investigation detail | GET | `/forensics/investigation/{audit_id}?window_hours=24` | forensics |
| Replay agent activity | GET | `/forensics/replay/{agent_id}?limit=50` | forensics |
| Timeline for an audit row | GET | `/forensics/timeline/{audit_id}` | forensics → flight_recorder |
| Blast radius for an agent | GET | `/forensics/blast-radius/{agent_id}?depth={n}` | forensics → identity_graph |
| Queue PDF export | POST | `/forensics/export/{audit_id}` | forensics |

## Auto-refresh & realtime

- **No auto-refresh.** The investigation list is loaded on mount and on agent change; subsequent actions are operator-driven.
- **No SSE.**

This is intentional. Forensics is reflective work — automatic polling would shuffle rows under the analyst's cursor mid-investigation.

## Per-agent scoping

Yes. URL parameter `?agent=...` takes precedence over the sidebar picker. Direct-linking from another page (e.g. the Agents profile → "Open in Forensics") opens the page pre-scoped to that agent.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No high-risk events in window | `No high-risk events found.` | Lower `min_risk` threshold or extend the window. |
| Selected agent has no high-risk events | `No high-risk events recorded for this agent.` | Same. |
| Replay panel empty | `No replay steps available.` | The agent has no recent audit rows. |

## Edge cases & known gotchas

- **Blast radius `Actor node not found`**: the forensics service maps agent_id → graph node_id internally, but if the agent has no graph node yet (e.g. a freshly-registered agent with no edges), the lookup fails. Seed at least one edge by running one `/execute` from Playground.
- **PDF export stuck in `queued`**: very large investigations can take 30+ seconds. The worker queue caps concurrent exports at 4. Inspect Settings → System Health if the queue is persistently slow.
- **Signed URL expires in 24 hours**: re-export to refresh. The PDF content is regenerated each time, so the signature is fresh.
- **Cross-source joins not transactional**: the audit row may be from 10 minutes ago; the identity graph state may have changed since. Timestamps are shown on every panel so the disparity is visible.
- **Read DSN routes to RDS replica**: on multi-AZ deployments, heavy forensics queries hit the read replica. The current dev deployment is Single-AZ (`acp-postgres-dev` only), so reads and writes share the same instance — expect heavier forensics queries to compete for the same connection pool until a replica is added.
- **Per-EC2 flap**: `/forensics/*` is proxied via `proxy_forensics` and is stable.

## Related docs

- [Forensics service](../../services/forensics.md)
- [Identity Graph service](../../services/identity-graph.md) — the source of blast radius
- [Flight Recorder service](../../services/flight-recorder.md) — the source of timeline detail
- [Audit service](../../services/audit.md) — the source of investigations
- [Tenant Data Requests](../../operations/tenant-data-requests.md) — the related compliance workflow

## Screenshot

![Forensics](../_screenshots/forensics.png)
