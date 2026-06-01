# Policy Sim

## What this page is for

A standalone, deeper version of the Simulate tab in Policy Builder. Run a draft Rego rule against historical events without affecting production traffic. Output is a side-by-side "what would have flipped" report.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/policy-sim`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN` or `SECURITY`. Simulate is computationally a read but is treated as a write in the platform's RBAC because the simulator runs OPA evaluation against the live policy bundle.

## What you see

- **Rule editor** — Rego text area with line numbers.
- **Agent picker** — UUID input plus the sidebar `useAgents` quick-select.
- **Time range picker** — 1 hour / 6 hours / 24 hours / 7 days.
- **Run button** — fires the simulation.
- **Result table** — decisions that would flip under the draft. Columns: tool, current decision, draft decision, finding.
- **Summary banner** — "No decision changes" if the draft is a no-op, or a per-tool count of flips.
- **Read-only banner** — "No OPA calls, no writes — read-only dry run" reinforces the safety guarantee.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Run a simulation | POST | `/policy/simulate` | policy → OPA |

## Auto-refresh & realtime

- **No auto-refresh.** Operator-driven.

## Per-agent scoping

Yes. The simulation always operates against one agent. Without a selection, the input is required.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Draft produces no flips | `No decision changes in the sampled events.` | Either the draft is a no-op or the window has no triggering events; widen the range. |

## Edge cases & known gotchas

- **Simulation timeout**: very wide windows (over 6 hours) can take 30+ seconds; capped at 1000 sampled events. If you need broader coverage, run multiple narrower windows.
- **403 on Run**: caller is `VIEWER` or `AUDITOR`. Re-login as `ADMIN` or `SECURITY`.
- **Rego syntax error**: surfaces in the response body with line/column. Fix and re-run.
- **Per-EC2 flap**: `/policy/simulate` is stable.

## Related docs

- [Policy service](../../services/policy.md)
- [Policy Builder UI](../primary/policies.md)
- [Policy Analytics UI](policy-analytics.md)

## Screenshot

![Policy Sim](../_screenshots/policy-sim.png)
