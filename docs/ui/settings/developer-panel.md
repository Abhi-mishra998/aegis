# Developer Panel

## What this page is for

The integrator's surface: create API keys for SDK callers, revoke compromised keys, and look up SDK / curl examples for every endpoint. Developers writing their first Aegis integration open this page to mint a key and see worked examples.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/developer`.
- **Keyboard hint**: `G D`.
- **Minimum role for read**: `AUDITOR` — listing existing keys is read.
- **Create and revoke** require `ADMIN`.

## What you see

- **API keys list** — table of existing keys with name, prefix (`acp_*`), created_at, last-used. The full key value is never displayed; only the prefix.
- **"Create key" form** — name field plus a Create button. On creation, the raw key is shown in a one-time toast that the operator must copy immediately.
- **Revoke action per row** — confirms before revoking.
- **SDK examples panel** — toggleable code blocks: Python `acp_client`, Node fetch, raw curl.
- **Common endpoints table** — the most-used routes with their required headers and one example each.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List keys | GET | `/api-keys` | api |
| Create key | POST | `/api-keys` | api |
| Revoke key | DELETE | `/api-keys/{id}` | api |

## Auto-refresh & realtime

- **No auto-refresh.** The list reloads after a create or revoke, not on a timer.

## Per-agent scoping

No. API keys are tenant-scoped, not agent-scoped. A key carries the issuing user's role; it does not grant any agent's permissions.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No keys yet | `No API keys yet` | Click "Create key" to mint one. |

## Edge cases & known gotchas

- **Raw key not shown a second time**: the raw key is returned in the create response and then discarded. Subsequent reads return only the prefix. Operators who lose the value must revoke and re-create.
- **Bcrypt hashed storage**: a leaked database does not expose usable keys.
- **No automatic key expiry**: keys live until explicitly revoked. Operators should rotate manually.
- **API key in URL**: never put a key in a query string. The platform accepts keys via the `Authorization: Bearer ...` header only.
- **Per-EC2 flap**: `/api-keys` is a stable proxy path.

## Related docs

- [API service](../../services/api.md)
- [Identity service](../../services/identity.md)
- [Quickstart](../../introduction/quickstart.md) — the curl examples mirror those on this page

## Screenshot

![Developer Panel](../_screenshots/developer-panel.png)
