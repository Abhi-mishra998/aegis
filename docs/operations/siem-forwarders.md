# SIEM Forwarders

*Aegis forwards every signed audit row to an external SIEM in real time. The audit
write path stays the source of truth; the SIEM is the operator's existing
console. This page covers the five shipped targets, where credentials live, and
how to enable each.*

## Why this page exists

Pre-Sprint-2b the README listed Splunk + Elastic + Sentinel + Chronicle as
SIEM integrations; the code only shipped Splunk + Datadog. The audit (C15)
flagged this as a CLAIM-ONLY half. Sprint 2b shipped the three missing
targets with real wire protocols, plus a credential loader that reads from
AWS Systems Manager Parameter Store so secrets never live in env files on
the audit container.

## Configuration

Two environment variables select the dispatcher path:

| Variable | Values | Purpose |
|---|---|---|
| `SIEM_TARGET` | `splunk` \| `datadog` \| `elastic` \| `sentinel` \| `chronicle` \| empty | Which forwarder to instantiate |
| `SIEM_CRED_SOURCE` | `env` (default) \| `ssm` | Where to pull credentials from |
| `SIEM_SSM_PREFIX` | string, default `/aegis-siem` | SSM Parameter Store prefix when `SIEM_CRED_SOURCE=ssm` |

When `SIEM_CRED_SOURCE=ssm`, the forwarder reads every parameter under
`{prefix}/{target}/` at boot. Parameter names are normalized to UPPER_SNAKE
(consistent with the `/aegis-prodha/*` SSM convention used elsewhere in
the account). The IAM role on the audit container needs
`ssm:GetParametersByPath` plus `kms:Decrypt` on the CMK that encrypts
the SecureStrings.

## Sprint 2b account state (ap-south-1)

The SSM parameters are provisioned but populated with `PENDING_REPLACE_WITH_*`
placeholders so an operator can flip `SIEM_TARGET=elastic` and immediately
see whether the path works against SSM, without leaking real credentials.

```
/aegis-siem/elastic/CLOUD_ID
/aegis-siem/elastic/API_KEY
/aegis-siem/sentinel/WORKSPACE_ID
/aegis-siem/sentinel/SHARED_KEY
/aegis-siem/chronicle/CUSTOMER_ID
/aegis-siem/chronicle/SERVICE_ACCOUNT_JSON
```

To populate one (example for Elastic):

```bash
aws ssm put-parameter \
  --region ap-south-1 \
  --name /aegis-siem/elastic/API_KEY \
  --type SecureString \
  --value "<base64 of id:key from Kibana>" \
  --overwrite
```

The integration test at `tests/integration/test_siem_ssm.py` auto-detects
non-PENDING values and runs the real-endpoint smoke test against them.

## Wire-protocol notes

### Splunk HEC

POST `{SPLUNK_HEC_URL}` with `Authorization: Splunk {token}`. One event per
request (HEC accepts batches via Newline JSON; the forwarder keeps the
simpler one-event-per-call shape for now). Source: `services/audit/siem.py::SplunkHECForwarder`.

### Datadog Logs API

POST `https://http-intake.logs.datadoghq.com/api/v2/logs` with `DD-API-KEY`.
Body is a JSON array of log events with `ddsource`, `ddtags`, `hostname`,
`service`, `message`. Source: `DatadogForwarder`.

### Elastic Cloud (Sprint 2b)

POST `{cluster_url}/_bulk` with `Authorization: ApiKey {base64}`. Body is
NDJSON of `{index: {...}}\n{event}\n` pairs. Cluster URL is decoded from the
Elastic Cloud ID locally — no `elasticsearch-py` dependency. Source:
`ElasticForwarder`. Per-document failures (rate-limited shards, mapping
errors) are surfaced via the `items` field in the Bulk API response and
counted as `item_failed` in Prometheus.

### Microsoft Sentinel (Sprint 2b)

POST `https://{workspace_id}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01`.
Auth is HMAC-SHA256 over a canonical string built from method,
content-length, content-type, x-ms-date, and resource. The shared key from
SSM is base64-decoded before HMAC. The `Log-Type` header names the custom
log table (default `AegisAudit`). Source: `SentinelForwarder`. Sentinel takes
2-5 minutes to surface a new custom-log table after the first event lands;
the smoke test allows for this.

### Google Chronicle (Sprint 2b)

POST `https://{region}-malachiteingestion-pa.googleapis.com/v2/udmevents:batchCreate`
with `Authorization: Bearer {access_token}`. The forwarder mints a Google-
service-account JWT locally (no `google-auth` dependency), exchanges it for
an OAuth2 access token at `https://oauth2.googleapis.com/token`, and caches
the token for its TTL. Events are mapped to a minimal UDM event with vendor,
product, principal user, and the full audit payload under `additional`.
Source: `ChronicleForwarder`. The integration test asserts the JWT contains
the right `iss`/`scope` and that the bearer token reaches the UDM endpoint.

## Observability

Three Prometheus counters cover every forwarder:

* `acp_siem_events_sent_total{target}` — incremented per successful event.
* `acp_siem_forward_errors_total{target, reason}` — incremented per failure,
  with `reason` set to `http_<status>`, `exception`, `item_failed`,
  `oauth_failed`, etc. so an alert can scope to a specific failure class.
* The audit writer's own `acp_audit_writes_total` is unaffected — SIEM
  failures never block the database write.

## AEVF back-reference fields (A6, 2026-06-14)

Every event forwarded to Splunk / Datadog / Elastic / Sentinel / Chronicle
now carries three additional fields, populated by
`SIEMEvent.from_audit_log()` in `services/audit/siem.py`:

```jsonc
{
  // … existing audit + SIEM fields …
  "aevf_bundle_url":   "https://ha.aegisagent.in/compliance/export/eu-ai-act?period_start=…&period_end=…",
  "aevf_event_hash":   "<sha256 hex>",
  "aevf_spec_version": "aevf/0.1.0"
}
```

- `aevf_bundle_url` is the verifiable day-bundle that contains this same
  audit event; the auditor pivots from the SIEM row to the bundle and runs
  `aegis-verify` offline.
- `aevf_event_hash` is the locator inside that bundle (matches the hash in
  the audit chain).
- `aevf_spec_version` pins the standard the bundle was built against
  (currently `aevf/0.1.0`).

`AEVF_PUBLIC_BASE_URL` env var on the audit container picks the public host
the bundle URL is rooted at (default `https://ha.aegisagent.in`). Self-host
customers point this at their own gateway so their auditor stays on their
infrastructure.

**Splunk saved-search example** that exposes a clickable verify link:

```spl
| eval verify = "<a href=" . aevf_bundle_url . " target=_blank>verify offline</a>"
```

See [AEVF Overview](../AEVF/README.md) for the standard, and
[Evidence Export Adapters](../integrations/evidence-export.md#5--aevf-back-reference-in-every-siem-record-a6) for the equivalent on the GRC and OTel exit paths.

## Testing

Three layers run in CI:

1. **Unit tests** (`tests/test_siem_extended.py` — 13 cases) — assert the
   exact wire shape per vendor: Elastic NDJSON, Sentinel HMAC + headers,
   Chronicle JWT + OAuth + UDM payload, plus token caching and the SSM
   loader.
2. **Real-AWS loader test** (`tests/integration/test_siem_ssm.py::test_loader_reads_placeholders_from_ssm`)
   — hits real SSM in `ap-south-1` and asserts the loader returns the
   six expected parameters. Runs on every CI invocation when AWS creds
   are available.
3. **Endpoint smoke tests** (same module, three tests) — skip with a
   pointed message when the SSM value is still `PENDING_*`. As soon as
   the operator overwrites the value with a real credential, the smoke
   test against Elastic / Sentinel / Chronicle runs without any pytest
   marker change.

## Failure modes

* **SSM permission denied** — the audit container's role is missing
  `ssm:GetParametersByPath` or `kms:Decrypt`. Surface: forwarder constructor
  raises and the audit service refuses to boot. Fix the IAM policy.
* **Placeholder values** — if `SIEM_CRED_SOURCE=ssm` and a parameter still
  holds `PENDING_REPLACE_WITH_*`, the dispatcher logs
  `siem_{target}_misconfigured` and the forwarder is a no-op. Audit writes
  continue normally.
* **Network egress blocked** — the audit container needs egress to the
  SIEM endpoint. Splunk and Sentinel use vendor-specific hosts; Elastic
  uses the customer's deployment host; Chronicle is region-specific. Run
  `curl -v $endpoint` from inside the container to confirm reachability.

## Operational checklist

- [ ] IAM role on the audit container has `ssm:GetParametersByPath` on
      `/aegis-siem/*` and `kms:Decrypt` on the SecureString CMK.
- [ ] `SIEM_TARGET` is set to one of the supported values.
- [ ] `SIEM_CRED_SOURCE=ssm` (recommended) or the env-var fallback is
      populated.
- [ ] The SSM parameter for the chosen target no longer starts with
      `PENDING_REPLACE_WITH_*`.
- [ ] Alertmanager has a rule on
      `rate(acp_siem_forward_errors_total[5m]) > 0` so silent forwarder
      regressions surface within the SLO window.

## Next

- [Key Rotation](key-rotation.md) — the SSM put-parameter pattern is the
  same one used for audit signing keys.
- [Cryptographic Audit Chain](../security/crypto-audit-chain.md) — the
  audit row this page forwards to SIEM.
