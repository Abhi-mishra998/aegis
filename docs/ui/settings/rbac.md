# RBAC Manager

## What this page is for

The grid view of "which agents own which tool permissions." Lists every agent, every tool in the platform catalog, and the ALLOW/DENY grant for each combination. Authors edit grants here rather than crafting POSTs to the registry by hand.

## Sidebar location & role gating

- **Sidebar group**: Settings → Access Control.
- **Path**: `/rbac`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Grant and revoke** require `ADMIN` or `SECURITY`.

## What you see

- **Agent search box** — filter rows by name or UUID.
- **Tool catalog selector** — the catalog of known tool names (from `/registry/tools`).
- **Grant table** — agent × tool grid. Each cell shows the action (`ALLOW` / `DENY`) plus the grantor and expiry timestamp.
- **"Grant" button per cell** — opens a small form with tool name, action, granted_by, expires_at.
- **"Revoke" button per cell** — confirms before deleting.
- **Per-agent permission count** — shown next to each agent name.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List agents | GET | `/agents` | registry |
| List tools catalog | GET | `/registry/tools` | registry |
| List permissions for an agent | GET | `/agents/{id}/permissions` | registry |
| Grant a permission | POST | `/agents/{id}/permissions` | registry |
| Revoke a permission | DELETE | `/agents/{id}/permissions/{perm_id}` | registry |

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, 30_000)` at `ui/src/pages/RBAC.jsx:284`.
- **No SSE.**

## Per-agent scoping

The page lists every agent in the tenant. The sidebar agent picker can deep-focus on one agent.

## Empty states

The grid renders even when an agent has zero permissions — every cell shows a Grant button. There is no platform-level empty state because the agents list is never empty for a configured tenant.

## Edge cases & known gotchas

- **409 on Grant**: a duplicate `(agent_id, tool_name)` exists. Use PATCH on the existing permission or revoke and re-grant.
- **403 on Grant or Revoke**: caller is `VIEWER` or `AUDITOR`. Re-login as `ADMIN` or `SECURITY`.
- **Permission cache 60-second lag**: a revoke takes up to 60 seconds to propagate to the gateway's permission cache. The kill switch is the fast path when seconds matter.
- **Expiry timestamps**: setting `expires_at` makes the grant automatically inactive after the moment. The row stays in the table with an "expired" badge.
- **Per-EC2 flap**: `/agents/{id}/permissions` is stable.

## Related docs

- [Registry service](../../services/registry.md)
- [Agents UI](../operations/agents.md)
- [User Management UI](user-management.md) — for managing human users (vs agents)

## Screenshot

![RBAC](../_screenshots/rbac.png)
