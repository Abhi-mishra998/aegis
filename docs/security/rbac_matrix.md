# Aegis — Canonical RBAC Matrix
*Authoritative spec. Every change to a gated endpoint must update this doc + the integration test in the same PR.*

## Role hierarchy

```
OWNER          # workspace billing + destructive ops (close workspace, delete tenant, transfer ownership)
ADMIN          # config: agents, policy packs, integrations, users, SSO
SECURITY_ANALYST  # forensics, audit, incidents, kill switch, approvals
DEVELOPER      # /execute, agent run, /v1/messages, runtime trust read
READ_ONLY      # dashboards, audit read, decision history; no writes
```

Subset relation: `OWNER ⊃ ADMIN ⊃ SECURITY_ANALYST ⊃ DEVELOPER ⊃ READ_ONLY` for **read** access. Write capabilities are explicit per endpoint.

API-key sessions (`acp_emp_*`) inherit the role minted on the key at issuance. A key with `role=DEVELOPER` cannot bypass `verify_role(Role.ADMIN)`.

## Enforcement contract

Every authenticated route in the gateway, identity, audit, registry, autonomy, decision, forensics, and api services declares an explicit role allow-list via `Depends(verify_role(Role.X, Role.Y, …))`. The default for any new route is **`verify_role(Role.OWNER)`** — most restrictive. Read endpoints may relax to `READ_ONLY` only after the matrix is updated.

Routes that legitimately need no role gate (`/health`, `/demo/spawn-workspace`, etc.) live in `_SKIP_PATHS` (`services/gateway/middleware.py`) and are listed in §6 of this doc.

## 1 · Workspace + identity

| Endpoint | Method | Allowed roles | Why |
|---|---|---|---|
| `/workspace/me` | GET | READ_ONLY+ | session info |
| `/workspace/inventory` | GET | READ_ONLY+ | dashboard list |
| `/workspace/system-values` | PATCH | OWNER | renames workspace, changes tier — billing-adjacent |
| `/workspace/exit-shadow-mode` | POST | OWNER | flips enforce mode for the whole tenant |
| `/workspace/apply-preset` | POST | OWNER | wholesale policy-pack swap |
| `/workspace/slack-config` | GET | ADMIN+ | reveals webhook url |
| `/workspace/slack-config` | PUT | ADMIN | sets approval routing |
| `/workspace/policy-packs` | GET | READ_ONLY+ | view current packs |
| `/workspace/policy-packs` | PUT | ADMIN | enable/disable packs |
| `/auth/me` | GET | any authed | identity probe |
| `/auth/tenants/{id}` | GET | OWNER, ADMIN | tenant metadata |
| `/auth/users` | GET | OWNER, ADMIN | user list |
| `/auth/users` | POST | OWNER | invite |
| `/auth/users/{id}` | PATCH | OWNER, ADMIN | change role |
| `/auth/users/{id}` | DELETE | OWNER | remove user |
| `/auth/sso/config` | GET | OWNER, ADMIN | reveals OIDC creds shape |
| `/auth/sso/config` | POST | OWNER | wire SSO provider |
| `/auth/clerk/provision` | POST | unauthed | post-signup bootstrap (own JWT) |
| `/webhooks/clerk` | POST | unauthed | Svix signature gate |

## 2 · Agents + registry

| Endpoint | Method | Allowed roles |
|---|---|---|
| `/agents` | GET | READ_ONLY+ |
| `/agents` | POST | ADMIN |
| `/agents/{id}` | GET | READ_ONLY+ |
| `/agents/{id}` | PATCH | ADMIN |
| `/agents/{id}` | DELETE | OWNER |
| `/agents/{id}/permissions` | GET | READ_ONLY+ |
| `/agents/{id}/permissions` | PUT | ADMIN |
| `/agents/{id}/quarantine` | POST | SECURITY_ANALYST+ |
| `/agents/{id}/release` | POST | SECURITY_ANALYST+ |
| `/registry/onboarding/*` | * | ADMIN |

## 3 · Decisions + execution

| Endpoint | Method | Allowed roles |
|---|---|---|
| `/execute` | POST | DEVELOPER+ (and API keys with `execute_agent`) |
| `/decision/history` | GET | READ_ONLY+ |
| `/decision/{id}` | GET | READ_ONLY+ |
| `/v1/messages` | POST | acp_emp_ key, role on key must be DEVELOPER+ |
| `/v1/chat/completions` | POST | acp_emp_ key, role on key must be DEVELOPER+ |
| `/v1/approvals/*` | GET/POST | acp_emp_ key, role on key must be DEVELOPER+ |

## 4 · Audit + forensics + compliance

| Endpoint | Method | Allowed roles | Why |
|---|---|---|---|
| `/audit/logs` | GET | READ_ONLY+ | read-only |
| `/audit/logs/{id}` | GET | READ_ONLY+ | drill-down |
| `/audit/logs/search` | POST | READ_ONLY+ | filtered read |
| `/audit/logs/export` | POST | SECURITY_ANALYST+ | bulk export — abuse-prone, see telemetry §3 |
| `/audit/logs/verify` | GET | READ_ONLY+ | chain integrity check |
| `/compliance/eu-ai-act` | GET | SECURITY_ANALYST+ | structured compliance read |
| `/compliance/soc2` | GET | SECURITY_ANALYST+ | same |
| `/compliance/nist-ai-rmf` | GET | SECURITY_ANALYST+ | same |
| `/compliance/export` | POST | OWNER | hands out the full evidence ZIP — high-trust |
| `/forensics/investigation/{id}` | GET | SECURITY_ANALYST+ | sensitive timeline |
| `/forensics/replay/{id}` | GET | SECURITY_ANALYST+ | replay any agent call |
| `/forensics/blast-radius/{agent_id}` | GET | SECURITY_ANALYST+ | identity-graph join |
| `/incidents` | GET | READ_ONLY+ |
| `/incidents/{id}` | PATCH | SECURITY_ANALYST+ | triage, assign, close |
| `/storylines` | GET | SECURITY_ANALYST+ | kill-chain narrative |
| `/iag/*` | GET | SECURITY_ANALYST+ | identity & access graph |
| `/replay/{request_id}` | GET | READ_ONLY+ | unified replay |

## 5 · Operations + integrations

| Endpoint | Method | Allowed roles |
|---|---|---|
| `/dashboard/state` | GET | READ_ONLY+ |
| `/notifications/count` | GET | READ_ONLY+ |
| `/notifications` | GET | READ_ONLY+ |
| `/notifications/{id}/ack` | POST | READ_ONLY+ (own only) |
| `/api-keys` | GET | OWNER, ADMIN |
| `/api-keys` | POST | OWNER, ADMIN |
| `/api-keys/{id}` | DELETE | OWNER, ADMIN |
| `/team` | GET | READ_ONLY+ |
| `/team/employees` | POST | OWNER, ADMIN |
| `/team/employees/{id}/keys` | POST | OWNER, ADMIN (mints acp_emp_*) |
| `/webhooks/config` | GET | OWNER, ADMIN |
| `/webhooks/config` | PUT | OWNER, ADMIN |
| `/webhooks/test/*` | POST | OWNER, ADMIN |
| `/siem/test` | POST | OWNER, ADMIN |
| `/siem/vendors` | GET | OWNER, ADMIN |
| `/sso/slack/*` | GET/POST | OWNER, ADMIN |
| `/billing/*` | GET | OWNER |
| `/billing/checkout` | POST | OWNER |
| `/billing/portal` | POST | OWNER |
| `/billing/stripe/webhook` | POST | unauthed (Stripe signature) |
| `/tenant/quota` | GET | READ_ONLY+ |
| `/auto-response/*` | GET | SECURITY_ANALYST+ |
| `/auto-response/*` | POST | SECURITY_ANALYST+ |
| `/autonomy/contracts` | GET | READ_ONLY+ |
| `/autonomy/contracts` | POST/PATCH/DELETE | ADMIN |
| `/autonomy/overrides` | GET | SECURITY_ANALYST+ |
| `/autonomy/overrides` | POST | SECURITY_ANALYST+ |
| `/autonomy/playbooks` | GET | SECURITY_ANALYST+ |
| `/autonomy/playbooks` | POST/PATCH | SECURITY_ANALYST+ |
| `/autonomy/playbooks/{id}/trigger` | POST | SECURITY_ANALYST+ |
| `/kill-switch` | POST | OWNER, SECURITY_ANALYST |
| `/admin/*` | * | OWNER (internal sales-eng only) |

## 6 · Routes intentionally unauthed (must stay in `_SKIP_PATHS`)

```
/health, /docs, /openapi.json, /redoc, /system/health, /status
/auth/token, /auth/login, /auth/agent/token, /auth/sso/providers
/events/stream (inline auth in handler)
/billing/stripe/webhook
/webhooks/clerk
/auth/clerk/provision
/demo/spawn-workspace
/demo/scenarios
/.well-known/security.txt (served by nginx)
```

`/metrics` is **NOT** unauthed — gated on `X-Mesh-Token` (ES256 mesh JWT) OR `X-Prometheus-Secret` (dedicated `PROMETHEUS_SCRAPE_SECRET`, independent of `INTERNAL_SECRET`). Post-N11 hardening (2026-06-21): the raw `INTERNAL_SECRET` lane was retired so a leak of the mesh secret can no longer scrape tenant-labelled counters.

## 7 · Cross-tenant invariants (every query)

Every SQL query against `audit_logs`, `agents`, `agent_credentials`, `api_keys`, `incidents`, `notifications`, `webhook_configs`, `policy_packs_enabled`, `tenants`, `users`, `flight_recorder_*`, `identity_graph_*`, `autonomy_*` MUST include `WHERE tenant_id = $1`. Any lookup-by-hash / lookup-by-id helper must additionally take `tenant_id` as a required argument.

Specific fixes shipped in EH-1:
- `services/api/repository/api_key.py::get_by_hash(key_hash, tenant_id)` — `tenant_id` is now required, never optional.

## 8 · Change procedure

1. Update this matrix doc.
2. Update the route decorator.
3. Update `tests/test_rbac_matrix.py` (parametrised over `(role, endpoint, expected_status)` cells).
4. Run `pytest tests/test_rbac_matrix.py` — must be all-green before merge.
5. The CI workflow `.github/workflows/test.yml` runs this on every PR.

## 9 · Out-of-scope (does NOT bypass)

- Internal service-to-service calls authenticated via `X-Internal-Secret` (or post-EH-5, ES256 mesh JWT) are still subject to data-layer tenant scoping (§7).
- Demo sessions (`is_demo=true` JWT) carry `role=OWNER` for **their own tenant only**. The matrix above applies normally to their own data; cross-tenant access is blocked at the tenant-scoping layer regardless of role.
