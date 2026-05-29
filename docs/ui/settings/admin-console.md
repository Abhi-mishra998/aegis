# Admin Console

## What this page is for

The platform-operator view across all tenants. Lists every tenant in the system, shows aggregate platform health, and renders the heatmap of decision activity across all tenants. It's the page someone running a multi-tenant Aegis SaaS opens to see who is using what.

## Sidebar location & role gating

- **Sidebar group**: Settings → Operations.
- **Path**: `/admin`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN` plus the `is_platform_admin` flag on the user row. A regular tenant `ADMIN` does not have platform-wide visibility; only the `is_platform_admin` user can list other tenants' summaries.

## What you see

- **Platform health banner** — same `/system/health` result as the System Health page, but compact.
- **Tenant list** — name, tier, RPS / daily / monthly caps, active agents count, recent decision count. Searchable.
- **Aggregate decision heatmap** — hourly grid across all tenants for the last 7 days.
- **Tenant action menu per row** — view detail, adjust caps, suspend (which is the platform-admin version of the per-tenant kill switch).

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Aggregate platform health | GET | `/system/health` | gateway |
| Cross-tenant decision summary | GET | `/audit/logs/summary` | audit |
| Tenant list | GET | `/admin/tenants` | api |
| Decision heatmap | GET | `/audit/logs/heatmap` | audit |

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, REFRESH_MS)` at `ui/src/pages/AdminConsole.jsx:195` where `REFRESH_MS=30_000`.
- **No SSE.**

## Per-agent scoping

No. Cross-tenant by design. The sidebar agent picker is ignored on this page.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| `/admin/tenants` returns empty | `No tenant data available.` | Tenant rows missing from `acp_identity.tenants` — seed via the identity service. |

## Edge cases & known gotchas

- **`/admin/tenants` returns 403 despite ADMIN role**: the user is tenant-ADMIN, not platform-ADMIN. Set `users.is_platform_admin=true` for cross-tenant view.
- **Heatmap empty for a tenant**: that tenant has no audit traffic in the window. Not a bug.
- **Suspend tenant action**: this is a platform-admin override of the kill switch, recorded as `action="platform_admin_tenant_suspended"` in the audit chain. The action is hard to reverse — confirm modal is intentionally heavyweight.
- **Per-EC2 flap**: `/admin/*` proxied via the strict-prefix nginx rule for `admin/`.

## Related docs

- [Identity service](../../services/identity.md)
- [Gateway service](../../services/gateway.md)
- [System Health UI](system-health.md)

## Screenshot

![Admin Console](../_screenshots/admin-console.png)
