# Autonomy

## What this page is for

The Autonomy page is where multi-agent contracts are written, edited, and audited. A contract is a tenant-level rule that bounds *how* agents are allowed to act in aggregate — delegation depth, cross-tenant access, time windows, cumulative cost caps. The page also shows the recent contract violations and the timeline of human override events (when an operator chose to allow a denied action).

This is the surface to use when "per-agent permissions are correct but the combination is unsafe."

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/autonomy`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Create, update, disable contract** requires `ADMIN` or `SECURITY`. The override timeline and violations table are read-only here; recording an override happens from the Audit Trail page's action menu.

## What you see

- **Contracts panel** — list of active and disabled contracts. Each row shows name, scope (agent pattern), delegation depth cap, cost cap, time window, status.
- **"New Contract" button** — opens an editor with: name, agent scope (glob), delegation_max_depth, cost_cap_usd + window seconds, time_window, cross_tenant_rules.
- **Violations panel** — recent contract violations (last 1440 minutes by default). Columns: contract, agent, reason, detected_at.
- **Overrides panel** — recent human override events (last 10080 minutes / 7 days). Columns: audit_id, overridden_by, reason, created_at.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List contracts | GET | `/autonomy/contracts` | autonomy |
| List violations (window in minutes) | GET | `/autonomy/violations?minutes={n}` | autonomy |
| List overrides | GET | `/autonomy/overrides?minutes={n}&limit={n}` | autonomy |
| Create contract | POST | `/autonomy/contracts` | autonomy |
| Update contract | PATCH | `/autonomy/contracts/{id}` | autonomy |
| Disable contract | DELETE | `/autonomy/contracts/{id}` | autonomy |

## Auto-refresh & realtime

- **All panels refresh**: every 30 seconds via `setInterval(fetchAll, 30_000)` at `ui/src/pages/AutonomyContracts.jsx:70`.
- **No SSE.** Violation arrival is checked at the next poll cycle.

## Per-agent scoping

No. Contracts are tenant-scoped by definition. Violations and overrides span all agents.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No contracts yet | `No autonomy contracts yet. Click New Contract above to start enforcing bounded autonomy.` | Use one of the built-in templates (delegation cap, cost cap, time window) as a starting point. |
| No violations in window | `no violations` | Healthy — no contract was tripped. |
| No overrides logged | `no overrides logged` | Operators have not used the override path. |

The public production demo currently has 0 contracts configured — the panels show empty states.

## Edge cases & known gotchas

- **Time window in UTC**: contracts that say "business hours only" run on UTC. A contract scoped to `09:00–17:00` blocks a tenant whose business hours are in a different time zone unless the spec is offset. The editor does not convert to local time today.
- **`cost_cap_window_seconds` is rolling-ish, not exact**: the contract check sums costs for the trailing window from the request time; granularity is per-minute aggregate. Sub-minute spikes are not blocked by the contract (stage 2 rate limit handles that).
- **Stage 7 evaluates contracts after stage 6**: a deny from policy at stage 4 is not affected by a contract; an allow can be downgraded to escalate or kill by a contract.
- **PATCH on `is_active`**: the editor toggles via `updateContract` not via a separate `disable` endpoint when changing `is_active`. The `DELETE /autonomy/contracts/{id}` path soft-disables; the row stays.
- **Delegation depth counts the immediate caller as 0**: agent A → agent B is depth 1, A → B → C is depth 2. A contract cap of 2 allows two-hop calls but not three.
- **Per-EC2 flap**: `/autonomy/*` proxied via `proxy_autonomy` in the gateway — stable.

## Related docs

- [Autonomy service](../../services/autonomy.md)
- [Gateway 10-Stage Pipeline](../../architecture/10-stage-pipeline.md) — where stage 7 evaluates contracts
- [Playbooks UI](playbooks.md) — sibling page for automated remediation
- [Audit Trail UI](../primary/audit-trail.md) — where overrides are recorded

## Screenshot

![Autonomy](../_screenshots/autonomy.png)
