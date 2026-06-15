# API Reference

*Every endpoint Aegis exposes. Generated from the live OpenAPI spec at `https://ha.aegisagent.in/openapi.json`. 201 operations across 35 tags.*

## How to use this page

Aegis is a single FastAPI app at `https://ha.aegisagent.in` (or `http://localhost:8000` for a local install). The OpenAPI spec is live at `/openapi.json` — this page is its human-friendly index.

For the full machine-readable spec including request/response schemas:

```bash
curl -sS https://ha.aegisagent.in/openapi.json | jq
```

For an interactive explorer, the Swagger UI is at `https://ha.aegisagent.in/docs` and Redoc at `https://ha.aegisagent.in/redoc`.

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

### `demo` (1 op)

| Method | Path | Purpose |
|---|---|---|
| POST | `/demo/groq-agent` | Run one end-to-end Groq-as-agent demo. Body: `{"prompt": "...", "session_id": "...", "scenario": "..."}` (session_id + scenario optional). Per-scenario agents auto-provision at the scenario's risk_level (R5 — fintech_data_egress=medium / devops_destruction=low / support_pii_exfil=medium). The route calls Groq server-side with the scenario-specific persona, then loops each suggested tool call through `/execute`. The UI's `/live-demo` page animates the trace client-side. See [Live Demo](../ui/primary/live-demo.md). |
| GET | `/demo/scenarios` | **Added 2026-06-13 (R5).** Returns the three live-demo scenarios with their labels, risk levels, agent names, and suggested prompts. Used by the UI scenario picker; a buyer can curl this to inspect the exact prompts the platform holds up against. |

### `audit` (33 ops)

The largest tag. Includes the audit log primary endpoints plus 24 aggregator endpoints for the dashboards.

| Method | Path | Purpose |
|---|---|---|
| GET | `/audit/logs` | Paginated audit rows. Accepts query filters `agent_id`, `action`, `decision`, `tool`, `start_date`, `end_date`, `limit`, `offset`. The UI's search panel migrated from POST `/audit/logs/search` to this GET on 2026-06-13 — the POST form was blocked by AWS WAFv2's SQLi managed rule whenever the body contained `"limit":N`. |
| POST | `/audit/logs/search` | Filtered search (still works for SDK callers that bypass the WAF, e.g. internal calls). Same filter set as the GET variant plus `metadata_filter`. |
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

### `compliance` (7 ops)

| Method | Path | Purpose |
|---|---|---|
| GET | `/compliance/eu-ai-act` | EU AI Act report (requires `period_start`, `period_end` query params) |
| GET | `/compliance/nist-ai-rmf` | NIST AI RMF report |
| GET | `/compliance/soc2` | SOC 2 report |
| GET | `/compliance/dpdp` | **Added 2026-06-14 (A5).** India DPDP Act, 2023 + DPDP Rules (Nov 2025) evidence bundle. Sections covered: §8(5)–8(9), §11, Rules Schedule II. Includes a `retention_claim` block honestly flagging whether `AUDIT_RETENTION_DAYS` meets the ≥365-day Rules minimum. |
| GET | `/compliance/tool-ledger` | Per-tool usage ledger |
| GET | `/compliance/export/{bundle_type}` | JSON evidence bundle download. `bundle_type ∈ {eu-ai-act, nist-ai-rmf, soc2, tool-ledger, dpdp, grc}`. Streaming, forwards `Content-Disposition`. For `bundle_type=grc`, also accepts `?format=json\|csv` (Vanta/Drata-style control-evidence export — A6). |
| GET | `/compliance/export/grc` | **Added 2026-06-14 (A6).** Vanta/Drata-style control-evidence export. Each evidence row carries `aevf_bundle_url`, `aevf_event_hash`, `aevf_spec_version` so the auditor can pivot from the GRC platform to the verifiable AEVF bundle. `?format=json` (default) returns a JSON object envelope; `?format=csv` returns RFC 4180 CSV (14 columns). |
| GET | `/compliance/verifiable-bundle/{framework}` | Self-contained AEVF bundle. `framework ∈ {eu-ai-act, nist-ai-rmf, soc2}`. Each row's `mappings` block now includes a `dpdp` key (A5). |
| POST | `/compliance/board-report` | Executive summary (streamed PDF) |
| POST | `/compliance/export` | PDF/JSON export by framework (legacy path, kept for SDK) |

### AEVF static assets (open standard, no auth)

| Method | Path | Notes |
|---|---|---|
| GET | `/aevf/spec.md` | **A2.** Byte-precise AEVF specification (`aevf/0.1.0`). Served as `text/plain; charset=utf-8` with permissive CORS. |
| GET | `/aevf/README.md` | Friendly AEVF introduction. |
| GET | `/aevf/auditor-checklist.md` | **A3.** 8-section auditor workpaper, ~25-min completion. Apache 2.0. |
| GET | `/aevf/reference-audit-report.md` | **A3.** Engagement-ready report template, three pre-drafted conclusions + sign-off page. Apache 2.0. |
| GET | `/aevf/reference-bundle-2026-06.json` | **A4.** Real, deterministic, signed AEVF bundle. 9 165 bytes. SHA-256 `8a6f09f65c374edf44c811dba8f146c8d79dab9ed74e3c49920be759951f20fc`. `aegis-verify --bundle …` → 6/6 PASS. |
| GET | `/aevf/` | HTML landing page linking everything above. |

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

- `https://ha.aegisagent.in/openapi.json` — production
- `http://localhost:8000/openapi.json` — local install

Spec metadata as of the most recent capture: **201 operations, 35 tags, 127.6 KB**. The spec is auto-generated by FastAPI from the route declarations; the moment a new route ships, the spec updates.

> **`/openapi.json` is served as `application/json` via an explicit `location =` block in `ui/nginx.conf` (added 2026-06-14).** Before that fix, the URL fell through the SPA's `location /` and returned the React `index.html` as `text/html`. If a buyer's `curl https://ha.aegisagent.in/openapi.json | jq` returns `parse error: Invalid numeric literal at line 1, column 11`, they have a cached UI image without the nginx fix — redeploy the UI per [Deployment](../operations/deployment.md).

## Live-verified endpoints (2026-06-14 prod-ha)

These were live-tested against `https://ha.aegisagent.in` with the documented admin credentials. Each `n/m` is `(observed-200-responses / total-hits)` across `m` requests through the ALB (so they cross both ASG hosts):

- `GET /openapi.json` → 20/20 with `content-type: application/json`, 127,627 B
- `GET /compliance/dpdp?period_start=…&period_end=…` → 30/30, ~23 KB DPDP bundle (Sections 8(5)-8(9), §11, Rules Schedule II)
- `GET /compliance/export/grc?format=json|csv&…` → 20/20 each variant, multi-MB body
- `GET /compliance/verifiable-bundle/{eu-ai-act|nist-ai-rmf|soc2}` → 200 with `format_version: "aegis-evidence-bundle/2026-06"` (DPDP not yet wired here; legacy bundle path is `/compliance/dpdp` above)
- `GET /demo/scenarios` → 200 with 3 R5 scenarios (`fintech_data_egress`, `devops_destruction`, `support_pii_exfil`)
- `GET /audit/logs/verify` → `{"valid": true, "is_integrous": true, "processed_count": 2493, "violations": []}`
- `GET /aevf/spec.md`, `/aevf/reference-bundle-2026-06.json`, `/aevf/auditor-checklist.md` — static, served by the UI nginx, `application/json` or `text/plain` content types
- `GET /system/health` → 12/12 healthy on 15/15 consecutive hits, p95 ~50 ms

## Next

- [Authentication](authentication.md) — how to mint and validate tokens
- [Error Codes](error-codes.md) — what every status code means and how to recover
- [Examples](examples.md) — curl, Python, Node samples for the common operations
- [Quickstart](../introduction/quickstart.md) — get started with curl
