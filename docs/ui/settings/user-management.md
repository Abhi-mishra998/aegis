# User Management

## What this page is for

The human-user CRUD surface. Invite a new user with a role, change an existing user's role, deactivate someone leaving the organization. The page is the human counterpart to the agent registry — humans live here, agents live on the Agents page.

## Sidebar location & role gating

- **Sidebar group**: Settings → Access Control.
- **Path**: `/users`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN`. Every action on this page is a write. `AUDITOR` and `VIEWER` cannot reach it.

## What you see

- **Invite form** — email field plus a role dropdown (`ADMIN` / `SECURITY` / `AUDITOR` / `VIEWER`).
- **Users table** — email, role, status (active / deactivated), last login, action menu.
- **Per-row action menu** — change role, deactivate, resend invite.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List users | GET | `/users` | identity |
| Invite a user | POST | `/users/invite` | identity |
| Update a user (role, status) | PATCH | `/users/{user_id}` | identity |
| Deactivate a user | DELETE | `/users/{user_id}` | identity |

## Auto-refresh & realtime

- **No auto-refresh.** The list reloads after a successful action.

## Per-agent scoping

No. Users are tenant-scoped, not agent-scoped.

## Empty states

The page does not render a meaningful empty state because every authenticated tenant has at least one user (the inviter).

## Edge cases & known gotchas

- **Invite email never received**: the invite is generated server-side as `/auth/users` plus a one-time token at `acp:user_invite:{token}` with a 24-hour TTL. If the email did not arrive, resend it from the action menu or send the invite link directly.
- **Cannot deactivate the last ADMIN**: the API rejects with 400 to prevent locking the tenant out. Promote another user first.
- **Role change does not invalidate existing JWT**: the user's existing 15-minute token retains its old role until expiry. For immediate role enforcement, revoke the user's tokens.
- **Per-EC2 flap**: `/users` is stable.

## Related docs

- [Identity service](../../services/identity.md)
- [RBAC UI](rbac.md) — for permission edits on agents
- [SSO Settings UI](sso-settings.md) — for tenants on SSO who don't manage local users

## Screenshot

![User Management](../_screenshots/user-management.png)
