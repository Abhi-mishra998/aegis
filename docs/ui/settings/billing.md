# Billing

## What this page is for

The tenant's spend dashboard plus the budget-request workflow. Shows current month spend, cap usage, anomalies, per-agent breakdown, and the queue of pending budget-cap-lift requests.

## Sidebar location & role gating

- **Sidebar group**: Settings → Account.
- **Path**: `/billing`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Create budget request**: `ADMIN` or `SECURITY`.
- **Approve / reject budget request**: `ADMIN` (and ideally a different admin than the requester for separation of duties).

## What you see

- **Spend tiles** — Today, This week, This month, % of cap consumed.
- **Daily-spend chart** — last 30 days.
- **Per-agent cost table** — top spenders ranked by USD.
- **Anomalies panel** — calls whose cost exceeded the agent's typical p95.
- **Invoice list** — past month-end invoices.
- **Budget requests panel** — pending requests with approve and reject buttons.
- **"Request budget" form** — for raising a cap-lift request.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Spend summary | GET | `/billing/summary?agent_id=...` | usage |
| Invoice list | GET | `/billing/invoices?agent_id=...` | usage |
| Usage dashboard | GET | `/usage/dashboard` | usage |
| Usage anomalies | GET | `/usage/anomalies` | usage |
| List budget requests | GET | `/billing/budget-requests` | usage |
| Create budget request | POST | `/billing/budget-requests` | usage |
| Approve budget request | POST | `/billing/budget-requests/{id}/approve` | usage + identity |
| Reject budget request | POST | `/billing/budget-requests/{id}/reject` | usage |

## Auto-refresh & realtime

- **Refresh interval**: `setInterval(...)` at `ui/src/pages/Billing.jsx:324`. Default 30 seconds.
- **No SSE.**

## Per-agent scoping

Yes. Selecting an agent narrows the summary and invoice list to that agent.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No budget requests | `No budget requests` | Healthy. |
| No agent costs | `No agent costs recorded` | Tenant has no audit traffic. |

## Edge cases & known gotchas

- **Spend tile shows zero but traffic is flowing**: usage worker not draining; check Settings → System Health for `acp_usage` health.
- **`audit_without_usage` gap**: a reconcile gap means audit rows exist without matching usage rows. Page surfaces a banner; the reconciler runs nightly to fix.
- **Cap-fire happens before UTC midnight in your time zone**: counters reset at UTC. Plan time-zone-sensitive workloads accordingly.
- **Budget request stuck pending**: no approver workflow wired (Slack notification didn't fire). Approve directly from the page or via the API.
- **Per-EC2 flap**: `/billing/*` and `/usage/*` are stable.

## Related docs

- [Usage service](../../services/usage.md) (billing is the cross-service flow described there)
- [Quota Management UI](quota-management.md)

## Screenshot

![Billing](../_screenshots/billing.png)
