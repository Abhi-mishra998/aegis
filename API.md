# HTTP API

All endpoints share the gateway base URL. Defaults:

* Local Docker: `http://localhost:8000`
* Reference deploy: `https://ha.aegisagent.in`

Auth: send `Authorization: Bearer <token>` for JWT, or `Bearer acp_…` /
`X-API-Key: acp_…` for API keys. Always include `X-Tenant-ID` (UUID).

Standard response envelope:

```json
{ "success": true, "data": <payload>, "error": null, "meta": null }
```

Errors return `success=false` with `error` populated and an HTTP 4xx/5xx
status. The 4xx set the gateway emits:

| Status | When |
|---|---|
| 400 | malformed request body, missing required field, invalid format |
| 401 | auth required / token revoked / `agent_revoked_by_remediation` |
| 403 | tenant mismatch, RBAC write-path denial, security block |
| 404 | unknown resource (incident, agent, IOC id) |
| 409 | conflict — incident has no participating agents, etc. |
| 422 | query param validation (compliance endpoints require date range) |
| 429 | rate limit hit (Retry-After header set) |

## Authentication

### `POST /auth/token`

Exchanges email + password for a JWT.

```bash
curl -sk -X POST https://ha.aegisagent.in/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  --data '{"email":"admin@acp.local","password":"…"}'
```

`data.access_token` is the bearer used by every subsequent call.

### `POST /auth/agent/token`

Mints a short-lived agent token; used by SDK wrappers on behalf of an
agent identity.

## Tool execution

### `POST /execute`

The single entry point for every agent tool call. Body:

```json
{
  "tool": "tool.name",
  "agent_id": "<uuid>",
  "arguments": { ... }
}
```

Headers: `Authorization`, `X-Tenant-ID`, optional `X-Session-ID`
(enables session-scoped storyline grouping).

Outcomes:

* `200` — allowed; upstream tool ran. Response carries the canonical
  decision in `data.security` + `data.governance`.
* `403` — denied or escalated. Body:
  ```json
  {
    "success": false,
    "error": "approval_required" | "policy_denied" | …,
    "data": {
      "findings": ["external_pii_exfil", "wire_above_hard_cap"],
      "policy_id": "SEC-EXFIL-001",
      "risk_score": 95,
      "tier": "deny" | "escalate" | "quarantine" | "kill"
    }
  }
  ```
* `429` — rate limit (`Retry-After` header set).
* `504` — decision-engine timeout (chained audit row stamped).

## Storylines (Sprint 4 — incident kill chains)

### `GET /storylines`

Query params: `since_minutes` (1–10080, default 1440), `limit` (1–500,
default 50). Returns open storylines for the caller's tenant,
newest-first.

```json
{ "items": [<Storyline>, …], "count": 14 }
```

### `GET /storylines/{incident_id}`

`incident_id` looks like `INC-1781522012-3f29c1a8`. 404 if unknown or
aged out of Redis (24 h TTL).

```json
{
  "incident_id": "INC-…",
  "status": "blocked" | "quarantined" | "open",
  "mitre_tactic_chain":    ["TA0007", "TA0009", "TA0010"],
  "mitre_technique_chain": ["T1087",  "T1213",  "T1567.002"],
  "participating_agents":  ["agA", "agB"],
  "steps": [ {seq, ts, agent_id, signal_id, mitre_*, tier, policy_id, target, explanation}, … ],
  "blocked_at_step": 4,
  "blocking_policy_id": "SEC-EXFIL-001",
  "title": "Discovery → Collection → Exfiltration",
  "narrative": "Step 1 (T1087): agent agABCDEF1… target=customers — recon\n…",
  "risk_score": 95
}
```

## Identity & Access Graph (Sprint 5)

### `GET /iag/agents/{agent_id}`

Returns `BlastRadius` with `touched_resources=[]` — i.e. the full
accessible set for the agent. Use for baselining.

### `GET /iag/incidents/{incident_id}/blast-radius`

Walks the Sprint 4 storyline, unions participating-agent privileges,
and returns:

```json
{
  "agent_id": "…",
  "incident_id": "INC-…",
  "accessible_resources": ["customers", "orders", "vault/aws"],
  "touched_resources":    ["customers"],
  "untouched_resources":  ["orders", "vault/aws"],
  "criticality_score":    34,
  "by_kind":              { "table": 2, "vault_path": 1 },
  "resource_labels":      { "customers": "PII customer rows", … },
  "last_ingest_ts":       1781521200.0,
  "participating_agents": ["agA"]
}
```

`last_ingest_ts=0.0` means the IAG cache has not been seeded yet for
the tenant.

## Auto-Remediation (Sprint 6)

### `GET /remediation/policy`

Returns the tenant's `RemediationPolicy`:

```json
{
  "revoke_api_keys":     true,
  "kill_active_tokens":  true,
  "page_oncall":         false,
  "audit_log":           true,
  "webhook_url":         ""
}
```

### `PUT /remediation/policy`

Same shape; replaces the policy. `refresh_seconds` for `webhook_url`-
based feeds must be ≥ 60.

### `GET /remediation/incidents/{incident_id}`

Returns the chronological action ledger for one incident:

```json
{
  "incident_id": "INC-…",
  "items": [
    {"kind": "revoke_api_key",     "status": "done",    "result": "added agent to set", "ts": 1781…},
    {"kind": "kill_active_tokens", "status": "done",    "result": "published to acp:token:revocations"},
    {"kind": "page_oncall",        "status": "skipped", "result": "policy disabled"},
    {"kind": "audit_log",          "status": "done",    "result": "xadded to acp:audit:writes"}
  ],
  "count": 4
}
```

### `POST /remediation/incidents/{incident_id}/replay`

Force re-run for one incident. Appends fresh ledger rows; idempotency
markers are bypassed.

### `POST /remediation/dry-run`

Simulates without mutating Redis. Body: `{ incident_id?, agent_id? }`.
Returns the action set the executor *would* fire under the current
policy.

## Threat-Intel (Sprint 7)

### `GET /threat-intel/iocs?kind=&source=&limit=&include_global=true`

Lists IOCs for the tenant plus the curated `_global` overlay.

```json
{ "items": [<IOCRecord>, …], "count": 16 }
```

`kind` values: `exfil_host`, `c2_domain`, `offshore_token`,
`destructive_shell`, `malicious_path`, `privilege_token`.

### `POST /threat-intel/iocs`

```json
{
  "kind":     "exfil_host",
  "value":    "evil.example",
  "severity": "high"
}
```

For `destructive_shell` the `value` must be a Python regex; bad
patterns are rejected at write-time.

### `DELETE /threat-intel/iocs/{ioc_id}`

### `GET /threat-intel/feeds` / `PUT /threat-intel/feeds/{name}`

Operator-configured HTTP feeds. PUT body:

```json
{
  "url":             "https://feeds.example.com/iocs.txt",
  "format":          "text" | "json",
  "refresh_seconds": 3600,
  "enabled":         true
}
```

### `POST /threat-intel/refresh`

Runs the curated `global_defaults_providers` and stamps the GLOBAL
overlay. Used to seed a fresh deployment.

## Compliance evidence bundles

### `GET /compliance/{framework}?period_start=YYYY-MM-DD&period_end=YYYY-MM-DD`

Frameworks: `soc2`, `eu-ai-act`, `nist-ai-rmf`, `dpdp`. Date params
required. Returns a signed JSON bundle scoped to the period.

### `GET /compliance/tool-ledger`

Returns the per-tool decision ledger for the tenant's whole tenure.
Large (MB+).

### `POST /compliance/export?format=pdf|json|csv`

Renders the most recent bundle in the requested format.

### `GET /compliance/export/grc?format=json|csv`

Vanta / Drata / Secureframe / Hyperproof-compatible row export across
every framework.

## Receipts + transparency

### `GET /receipts/key`

Returns the active ed25519 receipt-signing public key (PEM) +
fingerprint. Used by `aegis-aevf` to verify a bundle.

### `GET /receipts/{audit_id}`

Returns the signed envelope for one audit row.

### `GET /transparency/verify-root?date=YYYY-MM-DD`

Asserts the daily Merkle root for that date. Structured failure object
on mismatch; never returns null.

### `GET /transparency/consistency?from=YYYY-MM-DD&to=YYYY-MM-DD`

Confirms the `prev_root_hash` chain between two daily roots.

### `GET /transparency/keys`

Returns the active key + every historical key (for post-rotation
receipt verification).

## Operator routes (admin scope only)

| Path | Description |
|---|---|
| `/admin/kill-switch` | engage / disengage the tenant blockade |
| `/admin/agents` | tenant-wide agent inventory |
| `/agents/{id}/permissions` | grant / revoke per-agent permissions |
| `/api-keys` | create / rotate / revoke per-tenant keys |
| `/autonomy/contracts` | manage bounded-autonomy contracts |
| `/autonomy/overrides` | append a human-override event |
| `/policy/*` | policy bundle CRUD (admin) |

## Rate limits

Per-tenant default: `requests_per_second=20`, `burst=40`, daily +
monthly counters. Tier classes `enterprise` / `premium` / `basic`
override the idempotency TTL window (24 h / 1 h / 5 min).

`429` responses include `Retry-After` and a structured body:

```json
{ "error": "rate_limited", "limit_type": "agent_rps", "reset_at": <epoch> }
```

## SSE event stream

### `GET /events/stream?token=<jwt>`

Per-tenant Server-Sent Events channel. Event types include
`decision`, `incident_updated`, `policy_change`, `kill_switch`.
Channel demux is handled in the gateway's PubSubManager
(`services/gateway/main.py`).

## OpenAPI

Each service ships `/openapi.json`. The gateway aggregates the public
surface; service-internal endpoints are not exposed there.

## See also

* [`docs/api/`](docs/api/) — long-form examples
* [`docs/AEVF/spec.md`](docs/AEVF/spec.md) — receipt + bundle wire format
* [`ARCHITECTURE.md`](ARCHITECTURE.md) — what runs behind each endpoint
