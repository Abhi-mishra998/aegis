# Policies

## What this page is for

The Policies surface is where authors write, simulate, test, and ship the Rego rules that drive stage 4 of the gateway pipeline. It is three pages working together — Policy Builder (write and ship), Policy Analytics (measure how the deployed rules behave), and Policy Sim (dry-run a draft before shipping it). An author should be able to:

1. Draft a new rule in the Builder.
2. Simulate it against historical traffic.
3. See the would-be allow/deny breakdown.
4. Run unit tests on the Rego.
5. Activate it.
6. Watch hit-rate and false-positive-rate trends in Analytics.

## Sidebar location & role gating

- **Sidebar group**: Primary nav.
- **Paths**: `/policy-builder` (Policies; primary nav entry), `/policy-analytics` (Settings → Operations), `/policy-sim` (Settings → Developer).
- **Keyboard hint**: `G P` (for the Builder).
- **Minimum role for read**: `AUDITOR`. For activation (`POST /policy/upload`), simulation (`POST /policy/simulate`), and test runs (`POST /policy/test`): `ADMIN` or `SECURITY`. A `VIEWER` opening Policy Builder sees the rules but every write button returns the platform's 403 explaining the role requirement.

## What you see

### Policy Builder (`/policy-builder`)

- **Header** — "Visual Policy Builder" at the top left; refresh / save / activate buttons at the top right.
- **Agent picker** — left side. Drives which agent's permissions and audit history feed the simulation.
- **Rule editor** — middle. Monaco editor with Rego syntax highlighting. Pre-loaded with the agent's current policy overlay.
- **Right side — three tabbed panes**:
  - **Simulate** — runs `POST /policy/simulate` and shows decisions that would flip under the draft.
  - **Test** — runs `POST /policy/test` (OPA unit tests) and shows pass/fail per case.
  - **History** — list of previous rule revisions for this agent.

### Policy Analytics (`/policy-analytics`)

- **Header** — "Policy Analytics" with an "Edit Policies" CTA that links to `/policy-builder`.
- **KPI band** — Total Decisions, Active Policies, Unused Policies, Block Rate.
- **Per-policy table** — rows for each policy with hit rate, false-positive rate, last-fired timestamp.
- **Charts** — hourly activity, decision trend, deny reasons, finding breakdown, escalation-rate trend.

### Policy Sim (`/policy-sim`)

- **Time-range selector** — 1 hour / 6 hours / 24 hours / 7 days.
- **Agent picker** — restricts the sample.
- **Run button** — fires `POST /policy/simulate` with the current draft.
- **Result panel** — table of decisions that would flip, plus a summary line ("No decision changes — this policy produces identical outcomes to the current one for all N sampled events").

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load agent's current permission set | GET | `/agents/{id}/permissions` | registry |
| Save a permission grant | POST | `/agents/{id}/permissions` | registry |
| Run a simulation | POST | `/policy/simulate` | policy → OPA |
| Run unit tests on the Rego | POST | `/policy/test` | policy → OPA |
| Activate / upload a policy | POST | `/policy/upload` | policy → bundle server |
| Load Analytics aggregates | GET | `/audit/logs/summary`, `/audit/logs?limit=200`, `/audit/tool-breakdown`, `/audit/hourly-activity`, `/audit/decision-trend`, `/audit/deny-reasons`, `/audit/finding-breakdown`, `/audit/escalation-rate-trend` | audit |

## Auto-refresh & realtime

- **Policy Analytics**: 30-second poll via `setInterval(load, 30_000)` at `ui/src/pages/PolicyAnalytics.jsx:406`.
- **Policy Builder and Policy Sim**: no auto-refresh. Operations are author-driven; the page re-fetches only when the operator clicks Run.
- **No SSE.** Policy changes propagate through OPA's 60-second bundle poll, not a live channel.

## Per-agent scoping

Yes — all three pages scope to the agent selected in either the sidebar picker (`useAgents`) or a page-local agent picker. Policy Builder always operates against one agent. Policy Sim and Policy Analytics can run across all agents in the tenant (when the picker is cleared).

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Simulation produces no decision changes | `No decision changes — this policy produces identical outcomes to the current one for all N sampled events.` | Either the draft is identical in effect to the current policy, or the window has no triggering events. Extend the window. |
| Simulation window has no audit data | `No audit events found for this agent in the selected window.` | Use a longer window or an agent that has run recently. |
| Builder no rule activated yet | Editor empty with placeholder Rego | Start from a template via the "Insert template" picker. |
| Analytics no decision data | `No decision data in window` | Tenant has no audit traffic; trigger one `/execute` from Playground. |
| Analytics no findings | `No finding data` / `No deny reason data` | Expected for a clean tenant; the section hides if the dataset is empty. |

## Edge cases & known gotchas

- **403 on upload / simulate / test**: caller is not `ADMIN` or `SECURITY`. Re-login with the appropriate role.
- **OPA bundle stale**: a freshly-activated policy takes up to 120 seconds to be live across all gateway workers (bundle server poll + OPA poll). The Builder shows a "Bundle pending" badge until the platform confirms the new revision.
- **Simulation timeout**: very wide windows (over 6 hours) can take 30+ seconds to compute. The Sim page caps at 1000 sampled events to keep the request bounded.
- **Editor stuck after save**: the page does not block edits while save is in flight; a slow upload can race with a follow-up edit. Refresh the page if the editor state diverges from the activated policy.
- **`/audit/logs?limit=200` returns more than 200**: the API caps at the limit; if the response includes a `next_offset`, the UI paginates internally.

## Related docs

- [Policy service](../../services/policy.md)
- [Audit service](../../services/audit.md) — aggregator endpoints for Policy Analytics
- [Decision service](../../services/decision.md) — the consumer of every policy decision
- [OPA Policies](../../security/opa-policies.md) — the Rego files shipping with the platform
- [Threat Scenarios](../../security/threat-scenarios.md) — the rules that block the 4 shipped attack cases

## Screenshot

![Policy Builder](../_screenshots/policy-builder.png)
