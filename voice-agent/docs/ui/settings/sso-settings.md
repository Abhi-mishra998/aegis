# SSO Settings

## What this page is for

The configuration surface for SAML 2.0 / OIDC single sign-on. Operators come here to point Aegis at their identity provider (Google, Microsoft Entra, Okta, etc.) so users sign in with corporate credentials rather than local passwords.

## Sidebar location & role gating

- **Sidebar group**: Settings → Access Control.
- **Path**: `/sso`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN`. SSO config is foundational; only tenant admins should edit.

## What you see

- **Provider list** — toggleable cards for each supported provider.
- **OIDC config form** — discovery URL, client ID, client secret, scopes.
- **SAML config form** — metadata URL, SSO URL, certificate, attribute mapping.
- **Test button** — fires `POST /auth/sso/config/test` to verify the IdP is reachable.
- **Save button** — persists the config to Redis (`acp:sso_config:{tenant_id}`) and emits an audit row.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List enabled providers | GET | `/auth/sso/providers` | identity |
| Read current config | GET | `/auth/sso/config` | identity |
| Save config | POST | `/auth/sso/config` | identity |
| Test config reachability | POST | `/auth/sso/config/test` | identity |

## Auto-refresh & realtime

- **No auto-refresh.** Configuration changes are manual.

## Per-agent scoping

No. SSO is tenant-level.

## Empty states

The form renders even when no SSO is configured. There is no meaningful empty state.

## Edge cases & known gotchas

- **Save returns 400 "X-Tenant-ID required"**: the gateway proxy strips the tenant header in some edge cases. The UI always sends it; if missing, check the gateway proxy at `services/gateway/main.py::get_sso_config_proxy`.
- **Test button returns IdP error**: the discovery URL is unreachable from the EC2. Check egress and DNS from the `acp_identity` container.
- **Users redirected to `/auth/sso/{provider}/callback` see 500**: the callback's tenant-resolution config is missing or stale. Re-save the config.
- **Client secret displayed**: the form deliberately hides the saved secret (shows `••••••`); only a fresh save reveals the new value to the operator at submit time. The platform's policy is never to render secrets after persistence.
- **Per-EC2 flap**: `/auth/sso/*` proxies via the strict-prefix `auth` rule; stable.

## Related docs

- [Identity service](../../services/identity.md)
- [JWT auth](../../security/jwt-auth.md)
- [User Management UI](user-management.md)

## Screenshot

![SSO Settings](../_screenshots/sso-settings.png)
