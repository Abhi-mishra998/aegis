# Quota Management

## What this page is for

The view of the tenant's quotas: requests-per-second, burst, daily and monthly request caps, and the daily inference USD cap. Shows both the configured limits and the current consumption. Operators come here to confirm a tenant is well under cap, or to spot a tenant approaching one.

## Sidebar location & role gating

- **Sidebar group**: Settings → Operations.
- **Path**: `/quota`.
- **Keyboard hint**: none.
- **Minimum role**: `AUDITOR`.
- **Editing the caps** happens on the Admin Console for tenant-level changes, or via Identity's `PATCH /auth/tenants/{tenant_id}`.

## What you see

- **RPS tile** — current limit + a 24-hour usage line.
- **Burst tile** — burst capacity, recent peak.
- **Daily request cap** — limit + today's count + % consumed.
- **Monthly request cap** — same for the month.
- **Daily inference cost cap** — USD limit + today's spend + % consumed.
- **Degraded-mode policy chip** — shows `block_high_risk` / `block_all` / `allow_with_audit`.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Get quota state | GET | `/tenant/quota` | identity |

The single endpoint returns the full quota object: caps and current counters in one payload.

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, 30_000)` at `ui/src/pages/QuotaManagement.jsx:80`.
- **No SSE.**

## Per-agent scoping

No. Quotas are tenant-level. Per-agent caps are surfaced on the Agents page and on the Billing dashboard.

## Empty states

The page does not render a meaningful empty state — every tenant has at least default caps.

## Edge cases & known gotchas

- **Monthly counter doesn't reset on the 1st**: counters reset at UTC midnight on the 1st of the month. Tenants in non-UTC zones see the cutover one local-time boundary later.
- **80% warning fires once per month per tenant**: tracked via `acp:billing_alerts`. If a tenant approaches cap multiple times, only the first crossing emits a warning.
- **Cap fires at 99% instead of 100%**: per-day rollover boundary or per-minute aggregate granularity. Expected.
- **Per-EC2 flap**: `/tenant/quota` is stable.

## Related docs

- [Identity service](../../services/identity.md)
- [Billing UI](billing.md)
- [Admin Console UI](admin-console.md)

## Screenshot

![Quota Management](../_screenshots/quota-management.png)
