# System Health

## What this page is for

A live dashboard of "is each platform service responding right now." An on-call engineer opens it during an incident to find which service is the source of the trouble; an operator opens it during a deploy to confirm everything came back up.

## Sidebar location & role gating

- **Sidebar group**: Settings → Operations.
- **Path**: `/system-health`.
- **Keyboard hint**: `G H`.
- **Minimum role**: any authenticated role. The underlying `/system/health` endpoint is intentionally low-friction so even a `VIEWER` can confirm a healthy stack.

## What you see

- **Overall status banner** — top of page. Green if every service is healthy, amber if any service is degraded, red if any service is unhealthy.
- **Per-service tiles** — one tile per backend service with: name, status badge, last-success timestamp, response-time p95, the route checked.
- **Aggregate latency block** — p50, p95, p99 across the whole platform.
- **Active alerts** — any Prometheus alerts currently firing. Each alert links to the relevant runbook.
- **Kill switch indicator** — chip at the top showing whether the kill switch is currently engaged.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Aggregate health | GET | `/system/health` | gateway (deep probe of every downstream) |

The gateway's `/system/health` consults every downstream service and aggregates the results into one response. The UI never calls the per-service healths directly.

## Auto-refresh & realtime

- **30-second poll** via `setInterval(() => fetchHealth(), 30_000)` at `ui/src/pages/SystemHealth.jsx:158`.
- **No SSE.** Health checks are cheap; polling is fine.

## Per-agent scoping

No. Platform-wide health is tenant-agnostic.

## Empty states

The page does not have a meaningful empty state — `/system/health` always returns a response. A failed fetch shows "System health check unavailable" as a banner.

## Edge cases & known gotchas

- **All services green but customers complaining**: the health check is a deep probe but not an end-to-end probe. A slow downstream tool (Anthropic, Groq) won't show here. Cross-reference Settings → Observability for latency.
- **One service amber for over an hour**: degraded mode kicked in for that service. The audit chain still records every consult with `service_status="degraded"` so behavior is observable.
- **`/status` vs `/system/health`**: `/status` is the public customer-visible page and only carries gateway-internal latency. `/system/health` is the operator view with downstream RTTs. Don't confuse the two.
- **Per-host flap**: `/system/health` is gateway-internal so every gateway worker sees the same RDS / Redis state. The current dev deployment runs one EC2; on a multi-EC2 deployment, disagreement between hosts would point at a Docker DNS issue on one of them.

## Related docs

- [Gateway service](../../services/gateway.md)
- [Deployment Topology](../../architecture/deployment-topology.md)
- [Observability UI](observability.md)

## Screenshot

![System Health](../_screenshots/system-health.png)
