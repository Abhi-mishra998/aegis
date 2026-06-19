# UI Map

*Every page in the Aegis React UI, grouped by sidebar location, with the backing service for each.*

## Sidebar structure

The UI sidebar has three groups, in left-to-right order of how often operators use them:

- **Primary nav** (5 items) — the daily-driver pages.
- **Operations dropdown** (12–13 items) — power-user surfaces.
- **Settings hub** — 17 sub-pages reachable from the Settings page.

The full sidebar source is at `ui/src/components/Layout/Sidebar.jsx`. The agent-scope selector shown at the top of the sidebar (and mirrored in the topbar on wide layouts) is the shared `AgentScopePicker` component — see [UI Primitives](../architecture/ui-primitives.md) for the wider set of reusable components and the conventions they follow.

## Topbar surfaces

Some entry points live in the topbar rather than the sidebar:

| Surface | Source | Purpose |
|---|---|---|
| Agent scope picker | `ui/src/components/Layout/AgentScopePicker.jsx` | Tenant / agent scope, mirrored from the sidebar on wide layouts |
| SSE live/syncing pill | inline in `Topbar.jsx` | Live indicator for the Server-Sent Events stream |
| Command palette trigger (`Cmd-K`) | `ui/src/components/Common/CommandPalette.jsx` | Fuzzy nav over every page |
| Incidents badge | inline in `Topbar.jsx` | Red count + glow when open incidents > 0 |
| Notification center | `ui/src/components/Common/NotificationCenter.jsx` | Bell with unread count |
| User menu | inline in `Topbar.jsx` | Email + role + logout |

## Primary nav (5 pages)

| Page | Sidebar path | Hint | Backend services | Page |
|---|---|---|---|---|
| Flight Recorder | `/flight-recorder` | G F | flight_recorder, audit | [Flight Recorder](primary/flight-recorder.md) |
| Policies | `/policy-builder` | G P | policy, audit | [Policies](primary/policies.md) |
| Audit Trail | `/audit-logs` | G A | audit, identity | [Audit Trail](primary/audit-trail.md) |
| Incidents | `/incidents` | G I | api, audit | [Incidents](primary/incidents.md) |
| Settings | `/settings` | G S | — (nav only) | [Settings Hub](primary/settings-hub.md) |

## Operations dropdown (13 pages)

| Page | Sidebar path | Hint | Backend services | Page |
|---|---|---|---|---|
| Agents | `/agents` | — | registry | [Agents](operations/agents.md) |
| Identity Graph | `/identity-graph` | G G | identity_graph | [Identity Graph](operations/identity-graph.md) |
| Threat Graph | `/threat-graph` | G T | gateway (IAG) | [Threat Graph](operations/threat-graph.md) |
| Autonomy | `/autonomy` | — | autonomy | [Autonomy](operations/autonomy.md) |
| Approval Inbox | `/approval-inbox` | — | audit + autonomy + gateway (SSE) | [Approval Inbox](operations/approval-inbox.md) |
| Forensics | `/forensics` | — | forensics | [Forensics](operations/forensics.md) |
| Playground | `/playground` | — | gateway + decision + registry | [Playground](operations/playground.md) |
| Live Feed | `/live-feed` | G L | gateway (SSE) + audit | [Live Feed](operations/live-feed.md) |
| Playbooks | `/playbooks` | — | autonomy | [Playbooks](operations/playbooks.md) |
| Auto Response | `/auto-response` | — | api + autonomy | [Auto Response](operations/auto-response.md) |
| Compliance | `/compliance` | — | audit + api | [Compliance](operations/compliance.md) |
| Open Source | `/open-source` | — | (static) | [Open Source](operations/open-source.md) |
| Attack Sim | `/attack-sim` | — | gateway + decision | [Attack Sim](operations/attack-sim.md) |
| Kill Switch | `/kill-switch` | — | decision | [Kill Switch](operations/kill-switch.md) |

Kill Switch is shown only to users with `canViewKillSwitch` (ADMIN or SECURITY).

## Settings sub-pages (17 pages)

Grouped by the four sections of the Settings hub.

### Access Control (4)

| Page | Path | Service | Page |
|---|---|---|---|
| RBAC Manager | `/rbac` | registry + identity | [RBAC](settings/rbac.md) |
| User Management | `/users` | identity | [User Management](settings/user-management.md) |
| Security Ops | `/security` | audit + api | [Security Dashboard](settings/security-dashboard.md) |
| SSO Configuration | `/sso` | identity | [SSO Settings](settings/sso-settings.md) |

### Operations (5)

| Page | Path | Service | Page |
|---|---|---|---|
| System Health | `/system-health` | gateway | [System Health](settings/system-health.md) |
| Observability | `/observability` | audit + decision + insight | [Observability](settings/observability.md) |
| Admin Console | `/admin` | api + identity | [Admin Console](settings/admin-console.md) |
| Policy Analytics | `/policy-analytics` | audit | [Policy Analytics](settings/policy-analytics.md) |
| Quota Management | `/quota` | identity | [Quota Management](settings/quota-management.md) |

### Developer (7)

| Page | Path | Service | Page |
|---|---|---|---|
| Developer Panel | `/developer` | api | [Developer Panel](settings/developer-panel.md) |
| Policy Simulation | `/policy-sim` | policy | [Policy Sim](settings/policy-sim.md) |
| Playbooks | `/playbooks` | autonomy | (covered in [Operations](operations/playbooks.md)) |
| Webhook Settings | `/webhook-settings` | api | [Webhook Settings](settings/webhook-settings.md) |
| SIEM Integration | `/siem` | api | [SIEM Settings](settings/siem-settings.md) |
| Threat Intelligence | `/threat-intel` | api + intelligence | [Threat Intelligence](settings/threat-intel.md) |
| Scheduled Reports | `/scheduled-reports` | api | [Scheduled Reports](settings/scheduled-reports.md) |

### Account (2)

| Page | Path | Service | Page |
|---|---|---|---|
| Usage & Billing | `/billing` | usage + audit + identity | [Billing](settings/billing.md) |
| Risk Engine (preview) | `/risk` | audit + learning | [Risk Engine](settings/risk-engine.md) |

## Footer items

The sidebar footer holds two pages that are not in any of the three groups:

| Page | Path | Service | Notes |
|---|---|---|---|
| Notifications | `/notifications` | api | Reached via the bell icon |
| (no page) | logout button | identity | Reached via the Sign Out button |

## Sidebar paths that are also API paths

Several sidebar paths collide with API paths because the platform's REST routes use the same noun (`/agents`, `/incidents`, etc.). The nginx config disambiguates via the `Sec-Fetch-Mode` header — browser navigation serves the SPA shell; XHR / fetch hits the gateway. See [Deployment Topology](../architecture/deployment-topology.md) for the nginx contract.

## Reading order

A new operator should read in this order:

1. [Audit Trail](primary/audit-trail.md) — the durable record they will live in.
2. [Flight Recorder](primary/flight-recorder.md) — per-execution detail when something goes wrong.
3. [Incidents](primary/incidents.md) — the working surface during an incident.
4. [Playground](operations/playground.md) — to safely test rules and attacks.
5. [Identity Graph](operations/identity-graph.md) — blast-radius queries during triage.
6. [Forensics](operations/forensics.md) — deeper investigation.
7. [Kill Switch](operations/kill-switch.md) — emergency lever (read before you ever press it).
8. [Settings Hub](primary/settings-hub.md) — the navigation entry to the 17 Settings sub-pages.

## Cross-references

- The full sidebar source: `ui/src/components/Layout/Sidebar.jsx`.
- The router config: `ui/src/App.jsx`.
- The shared `useAgents` hook for per-agent scoping: `ui/src/hooks/useAgents.js`.
- The shared `useSSE` hook for Live Feed reconnection: `ui/src/hooks/useSSE.js`.
