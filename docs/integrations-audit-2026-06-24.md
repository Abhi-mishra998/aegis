# Enterprise Integrations Audit — 2026-06-24

Audit of the three integration surfaces enterprise buyers ask about before signing:
**SSO/Okta**, **SIEM (Splunk/Datadog)**, **SCIM provisioning**.

Scope was end-to-end: backend route + gateway proxy + UI flow + happy-path probe.
Crypto/receipt stack was not touched.

---

## 1. SSO / Okta

### What works today

- Backend OIDC implementation in `services/identity/oidc.py` is **production-grade**:
  - PKCE (S256) for the auth-code flow
  - `id_token` signature verified against the IdP JWKS (no `alg: none`, no HSxx confusion)
  - JWKS auto-rotation on unknown `kid`
  - HMAC-signed state token with 10-minute TTL for CSRF defense
  - Built-in support for Google, Microsoft, Okta (via env-var-configured client_id/secret)
- Per-tenant SSO config UI at `/settings?tab=sso` (`ui/src/pages/SsoSettings.jsx`) lets the operator pick SAML or OIDC, paste credentials, save to Redis, and run a "Test connection" reachability probe.
- Backend endpoints exposed via gateway:
  - `GET  /auth/sso/providers` — env-configured providers (public, drives Login buttons)
  - `GET  /auth/sso/config` — per-tenant config (secrets masked)
  - `POST /auth/sso/config` — save per-tenant config
  - `POST /auth/sso/config/test` — reachability probe for IdP metadata / OIDC discovery
  - `GET  /auth/sso/{provider}` — start OIDC flow
  - `GET  /auth/sso/{provider}/callback` — handle callback, mint ACP JWT
- Gateway proxy in `services/gateway/routers/sso.py` correctly forwards body + tenant context.

### What was broken or missing (now fixed)

- **`services/identity/router.py:1644`** — `POST /auth/sso/config/test` ignored the request body and only tested the Redis-stored config. The UI sends the current form values via `ssoService.testConfig(cfg)` (`ui/src/services/api.js:1198`), expecting the operator to be able to test BEFORE saving. The "Test connection" button therefore silently tested stale config; if the operator typed new credentials and clicked Test without first clicking Save, they were probing the previously-saved IdP.
- **`services/identity/router.py:1619`** — `POST /auth/sso/config` accepted masked secrets verbatim. The UI's GET endpoint returns `client_secret: "***abc12345"` to avoid leaking the real value; if a buggy client (or a future refactor) ever POSTed the GET response back, the stored secret would be overwritten with the placeholder, breaking the SSO flow on the next login attempt. The UI already strips `***`-prefixed values client-side, but server-side enforcement is the right boundary.
- **`ui/src/pages/SsoSettings.jsx:243`** — the test-result span rendered `testResult.error || 'Unreachable'`, but the backend returns `{reachable: false, issuer, status}` — there is no `error` field. On failure the operator saw a useless "Unreachable" label even when `status` was something actionable like `unreachable: ConnectError` or `http_404`.

### What I fixed

- `services/identity/router.py` `test_sso_config` now accepts an optional JSON body and overlays non-masked field values on top of the stored Redis config (`_SSO_CONFIG_FIELDS` field list, masked-secret stripping in one pass). Operator can hit "Test" with unsaved form values.
- `services/identity/router.py` `save_sso_config` now drops masked-prefix values for `certificate` and `client_secret` server-side (defense in depth).
- Extracted `_SSO_CONFIG_FIELDS` + `_SSO_MASKED_SECRET_FIELDS` constants so adding a new SSO field requires one change, not three.
- `ui/src/pages/SsoSettings.jsx` test-result span now falls back to `testResult.status` before "Unreachable".

### What I did not fix

- **Per-tenant Redis-stored SSO config is not actually wired into the OIDC login flow.** `enabled_providers()` reads from env vars (`OKTA_CLIENT_ID` etc.) only — Redis-stored configs do not appear in `/auth/sso/providers` and the login redirect cannot use them. The UI lets each tenant save their own Okta config but only env-configured providers are actually usable. Closing this gap is non-trivial: it requires dispatching `oidc._provider_cfg()` by `(tenant_id, provider)` instead of by `provider`, and adding a per-tenant cache. **Estimated sprint: 2-3 days.** Live probe (`curl https://aegisagent.in/auth/sso/providers` → `{"providers": []}`) confirms no env-provider is currently set on prod, so even the env-var path is dormant in production today.
- **No SAML wire implementation.** UI exposes SAML as an option but `services/identity/oidc.py` only implements OIDC. SAML 2.0 would need a separate library (`python3-saml` or similar). **Estimated sprint: 3-5 days** including ACS-URL handling and signed AuthnResponse verification.

---

## 2. SIEM (Splunk / Datadog)

### What works today

- Per-tenant UI at `/settings?tab=siem` (`ui/src/pages/SiemSettings.jsx`) lets the operator paste a Splunk HEC URL+token and a Datadog API key+site, save them, and click "Send test event" per target.
- Backend endpoints in `services/audit/compliance.py`:
  - `GET  /compliance/siem/config` — per-tenant config (secrets masked, `_mask` shows last 4 chars)
  - `POST /compliance/siem/config` — save per-tenant config
  - `POST /compliance/siem/test/splunk` — synthetic test event to Splunk HEC
  - `POST /compliance/siem/test/datadog` — synthetic test event to Datadog Logs Intake
  - `POST /compliance/siem/push` — manual backfill of last N audit rows
- Gateway proxy in `services/gateway/routers/compliance.py` correctly forwards body + tenant context.
- Forwarder code in `services/audit/siem_export.py` uses the correct Splunk HEC payload shape (`{"event": ..., "sourcetype": "acp:audit"}`) and Datadog Logs Intake v2 (`https://http-intake.logs.{site}/api/v2/logs`).
- A separate **global** SIEM forwarder (`services/audit/siem.py SIEMForwarder`) is wired into `services/audit/writer.py:194` and fires on every audit row write. It supports Splunk, Datadog, Elastic, Sentinel, Chronicle, and reads credentials from env vars or SSM (`SIEM_TARGET` + `SIEM_CRED_SOURCE`).

### What was broken or missing (now fixed)

- **`services/audit/compliance.py:1576/1604`** — `POST /siem/test/splunk` and `POST /siem/test/datadog` ignored the request body, identical bug to SSO. The UI sends the current form values via `siemService.testSplunk(cfg)` / `testDatadog(cfg)` (`ui/src/services/api.js:1110-1111`); the operator had no way to verify a new HEC token without saving first.
- **`services/audit/compliance.py:1553`** — `POST /siem/config` accepted masked secrets verbatim. Unlike the SsoSettings UI, the **SiemSettings UI did NOT strip masked values** before save (`ui/src/pages/SiemSettings.jsx:46`), so any user who hit Save without modifying the secret field would overwrite the real Splunk HEC token with `"***xxxx1234"`. Live-bug, not theoretical.
- **`services/audit/compliance.py:1738`** — `POST /siem/push` returned `{events_fetched, splunk: {...}, datadog: {...}}` with no top-level `status` or `sent` field. The UI's render-line `Pushed ${pushResult.sent ?? 0} events` therefore always displayed `Pushed 0 events`, even on a successful 500-event push.
- **`services/audit/compliance.py:1735`** — the unknown-target validation ran AFTER the work was done (after the splunk + datadog push attempts), so a `target=garbage` request would still push to both real targets before failing.

### What I fixed

- `_merge_siem_overrides()` helper applies non-masked body fields on top of stored Redis config; both `test_splunk_connection` and `test_datadog_connection` use it. Operator can test fresh credentials before saving.
- `save_siem_config` strips `***`-prefixed values for `splunk_token` and `datadog_key` server-side (defense in depth).
- `ui/src/pages/SiemSettings.jsx` `save()` now strips masked values before sending, matching the existing SsoSettings pattern (defense in depth on the client).
- `manual_siem_push` now returns a rolled-up `{status, sent, reason}` summary so the UI's "Pushed N events" / error chip renders accurately.
- Moved the unknown-target check BEFORE the push attempts so a bad request doesn't waste a Splunk HEC call.
- Extracted `_SIEM_CONFIG_FIELDS` + `_SIEM_MASKED_SECRET_FIELDS` constants for a single source of truth.

### What I did not fix

- **Per-tenant SIEM config is NOT wired into live event forwarding.** The `SIEMForwarder` invoked on every audit-row write reads ONLY from global env vars (`SPLUNK_HEC_URL` etc.) — it never reads the per-tenant Redis config the UI saves. A tenant who pastes Splunk credentials into the UI and clicks Save will see test events arrive in Splunk, but their real audit events will NOT be forwarded (only the operator's globally-configured target gets them). This is a multi-tenant correctness gap. Closing it requires either (a) dispatching `SIEMForwarder` per-tenant on each audit write — meaningful hot-path cost — or (b) a sidecar forwarder reading the Redis config. **Estimated sprint: 2 days** for option (a) with a per-tenant LRU cache.
- **No Elastic / Sentinel / Chronicle UI fields.** `siem.py` supports all five backends but the UI only exposes Splunk + Datadog. **Estimated sprint: 1 day** to add the three additional IntegrationCard sections + corresponding test endpoints.

---

## 3. SCIM (provisioning)

### What works today

- Nothing. There is no SCIM implementation in this repo.

### What was broken or missing

- `grep -ri "scim" services/ ui/src/` returns zero hits.
- `ui/src/components/settings/ScimTokensTab.jsx` does not exist.
- `/scim/v2/Users` is not registered with FastAPI; the live probe `curl -X PUT https://aegisagent.in/scim/v2/Users/foo` returns 401 from the gateway middleware (auth-required catch-all), not 404 from a real route.
- No SCIM-token model in `services/identity/models.py`.
- No SCIM tab in `ui/src/pages/Settings.jsx` (`TABS` array at line 46-58).

### What I fixed

- Nothing. SCIM is a green-field implementation, out of scope per the "don't implement bigger missing features" instruction.

### What I did not fix — sprint estimate

Building SCIM v2 from scratch is **3-5 days**:
1. New `services/scim/` micro-service or `services/identity/scim_router.py` module exposing:
   - `GET    /scim/v2/Users` (list, with `filter=userName eq "..."`)
   - `POST   /scim/v2/Users` (create)
   - `GET    /scim/v2/Users/{id}` (read)
   - `PUT    /scim/v2/Users/{id}` (replace)
   - `PATCH  /scim/v2/Users/{id}` (modify, RFC 7644 §3.5.2)
   - `DELETE /scim/v2/Users/{id}` (deactivate)
   - `GET    /scim/v2/Groups` + the same CRUD
   - `GET    /scim/v2/ResourceTypes` + `/scim/v2/Schemas` + `/scim/v2/ServiceProviderConfig` (metadata discovery)
2. SCIM-token mint/revoke endpoints (`POST /scim/tokens`, `DELETE /scim/tokens/{id}`) with a new `scim_tokens` table — distinct from the existing `api_keys` table so SCIM rate-limits and audit signals are separable.
3. Token-validator middleware on the SCIM router that accepts `Authorization: Bearer scim_<token>` and pins `request.state.tenant_id` from the token row.
4. Mapping layer: SCIM `User` resource ↔ `acp_identity.users` (handle `active`, `emails`, `displayName`, `name.givenName`/`familyName`, `userName` ↔ email, externalId ↔ idp_user_id).
5. `ui/src/components/settings/ScimTokensTab.jsx` with mint/revoke/list, copy-once token display, and a "SCIM base URL" callout (`https://aegisagent.in/scim/v2`).
6. Add SCIM tab to `ui/src/pages/Settings.jsx` under the Access & Identity group.
7. Conformance: at minimum the Okta SCIM 2.0 connector smoke-test (create / update / deactivate user round-trip).

Until SCIM ships, the customer-facing answer to "can I onboard in 1 day?" is "yes via our SSO JIT-provisioning" — the OIDC callback in `services/identity/router.py:1757` already upserts users on first login with `provider`, `email`, `name` from verified IdP claims. SCIM is the cleaner enterprise story (deprovision-on-leave matters) but JIT covers the day-1 case.

---

## Summary table

| Surface | Backend ready | UI ready | End-to-end works for buyer | Buyer-visible bugs fixed |
|---|---|---|---|---|
| **SSO / Okta** | Yes (OIDC + PKCE + JWKS verify) | Yes | Partial — env-var providers only, per-tenant config not wired into login flow | 3 (test-endpoint body ignored, masked-secret round-trip, unhelpful error label) |
| **SIEM** | Yes (Splunk + Datadog + Elastic + Sentinel + Chronicle in `siem.py`; legacy `siem_export.py` for per-tenant UI) | Splunk + Datadog only | Partial — per-tenant config saves + test-events work, but live audit forwarding only uses global env config | 4 (test-endpoint body ignored, masked-secret save corruption, push response shape, validation ordering) |
| **SCIM** | No | No | No | 0 (not implemented; documented sprint estimate) |

## Files changed

- `services/identity/router.py` — `test_sso_config` accepts body; `save_sso_config` strips masked secrets; new `_SSO_CONFIG_FIELDS` / `_SSO_MASKED_SECRET_FIELDS` constants
- `services/audit/compliance.py` — new `_merge_siem_overrides` helper + `_SIEM_CONFIG_FIELDS` / `_SIEM_MASKED_SECRET_FIELDS` constants; both `/siem/test/*` accept body; `save_siem_config` strips masked secrets; `/siem/push` returns rolled-up summary; validation moved before work
- `ui/src/pages/SsoSettings.jsx` — test-result fallback to `status`
- `ui/src/pages/SiemSettings.jsx` — strip masked values before save
