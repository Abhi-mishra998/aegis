# API Reference

*Every endpoint Aegis exposes. Generated from the live OpenAPI spec at `https://dev.aegisagent.in/openapi.json`. 201 operations across 35 tags.*

## How to use this page

Aegis is a single FastAPI app at `https://dev.aegisagent.in` (or `http://localhost:8000` for a local install). The OpenAPI spec is live at `/openapi.json` — this page is its human-friendly index.

For the full machine-readable spec including request/response schemas:

```bash
curl -sS https://dev.aegisagent.in/openapi.json | jq
```

For an interactive explorer, the Swagger UI is at `https://dev.aegisagent.in/docs` and Redoc at `https://dev.aegisagent.in/redoc`.

## Common request shape

Every authenticated request requires:

- **Authorization** — `Authorization: Bearer <jwt>` (preferred for SDK/curl callers) OR the `acp_token` HTTPOnly cookie (set automatically by browser logins).
- **X-Tenant-ID** — `X-Tenant-ID: <uuid>`. Required even on read paths.
- **X-Agent-ID** — `X-Agent-ID: <uuid>`. Required on `POST /execute`. Optional elsewhere.
- **Content-Type** — `application/json` for any body.

Other context-aware headers:

- `X-Request-ID` — carries across services for tracing; auto-generated if absent.
- `X-Trace-ID` — OpenTelemetry trace id.

## Common response shape

The platform wraps every JSON response in a structured envelope:

```json
{
  "success": true | false,
  "data":    <object | array | null>,
  "error":   "<string | null>",
  "meta":    { ... } | null
}
```

A success response sets `success: true` with the payload in `data`. A failure sets `success: false`, error in `error`, and optionally a structured `meta.details` array for validation errors. See [Error Codes](error-codes.md) for the matrix.

## Operations by tag

The 35 tags below group operations by domain. Each tag entry shows the count and the most-used routes.

### `auth` (11 ops)

User and agent authentication, SSO orchestration, tenant lookup.

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/token` | User login (email + password + X-Tenant-ID) |
| POST | `/auth/agent/token` | Agent login (agent_id + secret) |
| GET | `/auth/me` | Current user |
| POST | `/auth/introspect` | Token introspection |
| POST | `/auth/refresh` | Refresh token |
| POST | `/auth/revoke` | Force-revoke another user's token |
| POST | `/auth/logout` | Revoke own token |
| POST | `/auth/users` | Create user (ADMIN) |
| POST | `/auth/credentials` | Provision agent credentials |
| POST | `/auth/tenants` | Create or upsert tenant (platform-admin) |
| GET | `/auth/tenants/{tenant_id}` | Tenant metadata |

### `users` (4 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/users` | List users in the tenant |
| POST | `/users/invite` | Send an invite |
| PATCH | `/users/{user_id}` | Update role / status |
| DELETE | `/users/{user_id}` | Deactivate |

### `sso` (6 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/auth/sso/providers` | Enabled providers |
| GET | `/auth/sso/{provider}` | Start SSO login |
| GET | `/auth/sso/{provider}/callback` | OIDC callback |
| GET | `/auth/sso/config` | Current SSO config |
| POST | `/auth/sso/config` | Save SSO config |
| POST | `/auth/sso/config/test` | Test SSO connectivity |

### `agents` (10 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/agents` | List |
| GET | `/agents/summary` | KPI tiles |
| GET | `/agents/{agent_id}` | Detail |
| GET | `/agents/{agent_id}/profile` | Combined detail + aggregates |
| POST | `/agents` | Create |
| PATCH | `/agents/{agent_id}` | Update |
| DELETE | `/agents/{agent_id}` | Soft-delete |
| GET | `/agents/{agent_id}/permissions` | List tool grants |
| POST | `/agents/{agent_id}/permissions` | Grant |
| DELETE | `/agents/{agent_id}/permissions/{permission_id}` | Revoke |

### `registry` (1 op)

| Method | Path | Purpose |
|---|---|---|
| GET | `/registry/tools` | Catalog of known tool names |

### `execution` (2 ops)

| Method | Path | Purpose |
|---|---|---|
| POST | `/execute` | The main event — runs the 11-stage pipeline |
| POST | `/execute/{tool_name}` | Sugar variant with tool name in the path |

### `audit` (33 ops)

The largest tag. Includes the audit log primary endpoints plus 24 aggregator endpoints for the dashboards.

| Method | Path | Purpose |
|---|---|---|
| GET | `/audit/logs` | Paginated audit rows |
| POST | `/audit/logs/search` | Filtered search |
| GET | `/audit/logs/summary` | KPI tiles |
| GET | `/audit/logs/verify` | Chain verification |
| GET | `/audit/logs/{audit_id}/explain` | Decision explanation |
| GET | `/audit/logs/{audit_id}/notes` | List notes |
| POST | `/audit/logs/{audit_id}/notes` | Add note (ADMIN/SECURITY) |
| GET | `/audit/logs/heatmap` | Activity heatmap |
| GET | `/audit/logs/soc-timeline` | SOC feed |
| GET | `/audit/export`, POST `/audit/export` | CSV / PDF |
| GET | `/audit/trends`, `/audit/hourly-activity`, `/audit/weekly-heatmap` | Time-grid aggregates |
| GET | `/audit/decision-trend`, `/audit/deny-reasons`, `/audit/escalation-rate-trend`, `/audit/posture-score-trend`, `/audit/top-findings`, `/audit/finding-breakdown` | Decision aggregates |
| GET | `/audit/tool-breakdown`, `/audit/tool-risk`, `/audit/tool-usage/{agent_id}` | Tool aggregates |
| GET | `/audit/agent-activity`, `/audit/daily-active-agents`, `/audit/agent-findings/{id}`, `/audit/agent-daily-decisions/{id}`, `/audit/drift/{id}`, `/audit/peer-benchmark/{id}` | Agent aggregates |
| GET | `/audit/risk-trend/{id}`, `/audit/risk-histogram`, `/audit/risk-percentile-trend`, `/audit/high-risk-events` | Risk views |

### `risk` (5 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/risk/summary` | Risk overview tiles |
| GET | `/risk/timeline` | Risk score over time |
| GET | `/risk/top-threats` | High-risk row list |
| GET | `/risk/signal-weights` | Current weights |
| PUT | `/risk/signal-weights` | Override weights (ADMIN/SECURITY) |

### `decision` (5 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/decision/summary` | Aggregate counts |
| GET | `/decision/history` | Recent decisions |
| GET | `/decision/kill-switch/{tenant_id}` | Read kill switch |
| POST | `/decision/kill-switch/{tenant_id}` | Engage (ADMIN/SECURITY) |
| DELETE | `/decision/kill-switch/{tenant_id}` | Disengage |

### `policy` (3 ops)

| Method | Path | Purpose |
|---|---|---|
| POST | `/policy/simulate` | Replay a draft policy over historical events |
| POST | `/policy/test` | Run Rego unit tests |
| POST | `/policy/upload` | Persist a new or updated Rego file |

### `receipts` and `transparency` (3 + 8 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/receipts/key` | Public signing key |
| GET | `/receipts/{execution_id}` | Per-row signed receipt |
| POST | `/receipts/verify` | Verify an externally-archived receipt |
| GET | `/transparency/roots` | Daily roots list |
| GET | `/transparency/roots/{date}` | Specific date's root |
| GET | `/transparency/inclusion/{execution_id}` | Merkle inclusion proof |
| GET | `/transparency/consistency` | Day-over-day root chain check |
| GET | `/transparency/keys` | Current + historical signing keys |
| POST | `/transparency/verify-root` | Verify an externally-archived root |
| POST | `/transparency/compute` | Recompute today's root (operator) |

### `billing` (9 ops) and `usage` (4 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/billing/summary` | Spend tiles |
| GET | `/billing/invoices` | Invoice list |
| GET | `/billing/cost-attribution` | Per-agent breakdown |
| GET | `/billing/budget-requests`, `POST`, `POST /{id}/approve`, `POST /{id}/reject`, `GET /{id}` | Budget request workflow |
| POST | `/billing/events` | Internal-only emission path |
| GET | `/usage/dashboard`, `/usage/anomalies`, `/usage/summary`, `/usage/by-agent` | Dashboard payloads |

### `compliance` (6 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/compliance/eu-ai-act` | EU AI Act report |
| GET | `/compliance/nist-ai-rmf` | NIST AI RMF report |
| GET | `/compliance/soc2` | SOC 2 report |
| GET | `/compliance/tool-ledger` | Per-tool usage ledger |
| POST | `/compliance/board-report` | Executive summary |
| POST | `/compliance/export` | PDF export |

### `Incidents` (10 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/incidents` | List |
| GET | `/incidents/summary` | Counts |
| GET | `/incidents/transitions` | Valid state machine |
| GET | `/incidents/{id}` | Detail |
| GET | `/incidents/{id}/comments` | Comment thread |
| POST | `/incidents` | Open (internal) |
| PATCH | `/incidents/{id}` | Update status |
| POST | `/incidents/{id}/actions` | Record action |
| POST | `/incidents/{id}/comments` | Add comment |
| POST | `/incidents/{id}/export` | PDF |

### `ARE` (16 ops) — Auto Response

Rules, simulation, history, rollback, feedback, pending approvals, metrics, toggle, replay. See [Auto Response UI](../ui/operations/auto-response.md) for the human-facing flow.

### `playbooks` (9 ops) and `autonomy` (5 ops)

`playbooks/*` — CRUD plus trigger plus runs plus stats plus templates.
`autonomy/*` — contracts (full_path proxy) plus the `/playbooks/autotrigger-stats` static route.

### `flight`, `forensics`, `graph` (4 + 3 + 4 ops)

Generic-prefix proxies into the per-service routes (`/flight/timelines`, `/flight/timeline/{id}`, `/forensics/investigation`, `/graph/agents`, `/graph/blast-radius/{id}`, `/graph/compromise/simulate`, etc.). The exact sub-routes are documented in each service's docs ([Flight Recorder](../services/flight-recorder.md), [Forensics](../services/forensics.md), [Identity Graph](../services/identity-graph.md)).

### `webhooks` (5 ops), `siem` (5 ops), `threat-intel` (3 ops)

Outbound notification and SIEM forwarder configuration; threat-intel IP/domain enrichment.

### `reports` (7 ops)

Scheduled report CRUD plus run-now plus delivery history.

### `notifications` (5 ops)

In-platform notification feed.

### `events` (1 op)

| Method | Path | Purpose |
|---|---|---|
| GET | `/events/stream` | Server-Sent Events stream — Live Feed |

This is the only long-lived endpoint. It bypasses most middleware (auth happens inline in the route).

### `API Keys` (4 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api-keys` | List |
| POST | `/api-keys` | Create (raw key returned exactly once) |
| DELETE | `/api-keys/{key_id}` | Revoke |
| POST | `/api-keys/validate` | Validate a key (internal/SDK) |

### `admin` (2 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/tenants` | List all tenants (platform-admin) |
| GET | `/admin/tenants/{tenant_id}` | Tenant detail |

### `tenant` (1 op) and `security` (1 op)

| Method | Path | Purpose |
|---|---|---|
| GET | `/tenant/quota` | Tenant quota and current consumption |
| GET | `/security/posture` | Posture score and breakdown |

### `dashboard` (1 op) and `internal` (1 op)

| Method | Path | Purpose |
|---|---|---|
| GET | `/dashboard/state` | Saved dashboard filters |
| POST | `/internal/reconciliation-report` | Reconciler ingestion (internal-only) |

### `ops` (3 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/system/health` | Deep readiness with downstream probes |
| GET | `/status` | Public customer-visible status |

### `(untagged)` (1 op)

| Method | Path | Purpose |
|---|---|---|
| GET | `/metrics` | Prometheus scrape |

## Live spec

The authoritative spec is always at:

- `https://dev.aegisagent.in/openapi.json` — production
- `http://localhost:8000/openapi.json` — local install

Spec metadata as of the most recent capture: **201 operations, 35 tags, 104 KB**. The spec is auto-generated by FastAPI from the route declarations; the moment a new route ships, the spec updates.

## Next

- [Authentication](authentication.md) — how to mint and validate tokens
- [Error Codes](error-codes.md) — what every status code means and how to recover
- [Examples](examples.md) — curl, Python, Node samples for the common operations
- [Quickstart](../introduction/quickstart.md) — get started with curl
