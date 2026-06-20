# Okta SCIM provisioning — setup runbook

This document walks an Okta administrator through connecting their Okta org
to Aegis so that creating, deactivating, or moving a user in Okta
automatically mirrors into Aegis with no manual user list maintenance.

Sprint EI-3 (2026-06-20). Aegis side is RFC 7644 (SCIM 2.0) compliant for
the subset Okta's connector exercises — see the test suite at
`tests/test_ei3_scim_compliance.py` for the validated cases.

---

## Aegis side: issue a bearer token

1. Sign into Aegis as an **OWNER** (the SCIM-token UI is OWNER-only;
   ADMIN cannot see it because a leaked SCIM bearer grants full directory
   write to the entire tenant).
2. Go to **Settings → Integrations → SCIM (Okta)**.
3. Enter a label (e.g. `Okta-prod`) and click **Issue token**.
4. A banner appears with the plaintext token of the form
   `scim_abcdef0123456789abcdef`. **Copy this value now** — Aegis does
   not store the plaintext and cannot show it again. Dismiss the banner
   only after you have it in your clipboard.

Token format: `scim_<23 lowercase base32 chars>` (~110 bits of entropy).
The leading `scim_`
prefix is what the gateway middleware uses to route the bearer to the
SCIM validator instead of the JWT validator, so do not strip it.

---

## Okta side: create the app

1. In the Okta admin console, **Applications → Browse App Catalog →
   "SCIM 2.0 Test App (OAuth Bearer Token)"** (or any of Okta's generic
   SCIM 2.0 templates that supports `Authentication header`).
2. Set the application label to `Aegis`.
3. On the **Sign On** tab, keep defaults (SAML/OIDC not required for
   provisioning-only).
4. On the **Provisioning** tab, click **Configure API Integration**.

### Provisioning → Integration

| Field | Value |
|---|---|
| SCIM connector base URL | `https://aegisagent.in/scim/v2` |
| Unique identifier field for users | `userName` |
| Supported provisioning actions | ☑ Push New Users / Push Profile Updates / Push Groups |
| Authentication Mode | `HTTP Header` |
| Authorization | `Bearer scim_xxxxxxxxxxxxxxxxxxxxxx` (the full token from Aegis, including the `scim_` prefix) |

Click **Test API Credentials**. Okta hits `GET /ServiceProviderConfig`
on Aegis; expect green check. If you see a 401, the bearer is wrong or
the token was revoked.

### Provisioning → To App

Enable these:

| Setting | Value |
|---|---|
| Create Users | ☑ |
| Update User Attributes | ☑ |
| Deactivate Users | ☑ |
| Push Groups | ☑ (if you want Okta groups to land as Aegis Teams) |

### Provisioning → To App → Attribute mappings

Default Okta mappings already cover what Aegis reads:

| Okta attribute | Aegis User field |
|---|---|
| `userName` (email-shaped) | `email` (primary key, lowercased) |
| `givenName` + `familyName` | `full_name` (joined with single space) |
| `active` | `is_active` (Okta deactivate → `is_active=false`) |

You do not need to map roles. Aegis SCIM-provisioned users land with
role=`VIEWER`. Promote them to ADMIN / OWNER via the Aegis **Team** page
— role grants are deliberately not in the SCIM scope because a leaked
bearer must not be able to escalate any user to OWNER.

### Push Groups → Group mapping

Each Okta Group you assign the Aegis app to will land in Aegis as a
**Team** with the same `displayName`. Aegis Team rollups
(spend, harmful-blocked counts) follow the team membership Okta pushes.

---

## Verify end-to-end

1. In Okta: **People → Add Person** with email `scim-test@yourcompany.com`,
   assign the Aegis app to them.
2. Within ~30 seconds, refresh Aegis **Team** page. The new user should
   appear as `scim-test@yourcompany.com` with role=`VIEWER`.
3. In Okta: deactivate `scim-test`. Within ~30 seconds, the Aegis row
   shows `inactive`.
4. In Aegis **Settings → Integrations → SCIM (Okta)**, the `last used`
   timestamp on the bearer should be < 1 min ago.

If any of those three steps fails, check:

- **Okta provisioning history** — every error response from Aegis
  carries an RFC 7644 `detail` field that Okta logs verbatim. Common
  values:
  - `Missing Bearer token` — header not configured on Okta side
  - `SCIM token has been revoked` — re-issue and re-paste
  - `User 'x@y.com' already exists` — Okta is pushing a user Aegis
    already has; safe to ignore on first sync, otherwise fix the
    duplicate in one system

---

## Revocation + rotation

- **Revoke a bearer**: Settings → Integrations → SCIM (Okta) → Revoke.
  Okta's next provisioning call fails with 401; provisioning history
  shows the user-facing error. Issue + paste a new token.
- **Rotation cadence**: no Aegis-imposed schedule. Rotate when an Okta
  admin who knew the bearer leaves the org, OR every 365 days as a
  defence-in-depth practice. Aegis does not auto-expire SCIM tokens
  because Okta would silently stop provisioning, which is worse than a
  long-lived bearer behind disk-level KMS and a documented revocation
  path.

---

## What SCIM does NOT cover

- **Aegis API keys** (`acp_emp_…` employee virtual keys) are NOT
  provisioned by SCIM. They are minted per-user in the Aegis
  Developer Panel. SCIM is for directory identity only; runtime
  agent calls live on a separate auth track.
- **Aegis role** (OWNER / ADMIN / SECURITY_ANALYST / DEVELOPER /
  READ_ONLY) is NOT set by SCIM. New users land as VIEWER; an Aegis
  OWNER promotes them.
- **Per-team budgets** are managed in Aegis. SCIM mirrors group
  *membership*; the budget rows live on the Team row only.
