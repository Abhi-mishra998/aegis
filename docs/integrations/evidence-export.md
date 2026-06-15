# Aegis Evidence Export

> Single canonical inventory of every channel Aegis ships evidence
> through. Whatever the buyer's existing observability / SIEM / GRC
> stack, Aegis is a good citizen of it — we never replace the console
> they already pay for. The positioning sentence the strategy doc
> quotes:
>
> *"CloudWatch tells you what your agents did. Aegis decides what
> they're allowed to do — and we integrate with your observability,
> we don't replace it."*

**As of 2026-06-14 (A6), every SIEM event and every GRC evidence row
carries an AEVF back-reference** — `aevf_bundle_url`, `aevf_event_hash`,
`aevf_spec_version`. The buyer's auditor pivots from a Splunk row or a
Vanta control to the verifiable AEVF bundle and runs `aegis-verify`
offline. Aegis is the **evidence engine behind** the buyer's existing
workflow; see [AEVF Overview](../AEVF/README.md) for the open standard.

This page lives next to the integration code so a doc claim that
disagrees with what ships shows up in the same PR. The
`README` and `SECURITY.md` link here; do not duplicate the inventory
elsewhere.

---

## 1 · SIEM forwarders (`services/audit/siem.py`)

Five forwarders, all shipping today. The dispatcher reads `SIEM_TARGET`
from env (or SSM Parameter Store via `/aegis-siem/*`) and instantiates
the right backend.

| Target           | Class               | Authentication              | Notes |
|------------------|---------------------|-----------------------------|-------|
| Splunk HEC       | `SplunkHECForwarder` | HEC token                   | 5 s timeout, prom counters |
| Datadog Logs     | `DatadogForwarder`   | `DD-API-KEY`                | 5 s timeout, prom counters |
| Elastic Cloud    | `ElasticForwarder`   | Cloud ID + API key          | Bulk Index API, per-item failure surface |
| Microsoft Sentinel | `SentinelForwarder` | HMAC-SHA256 (Workspace key) | Log-Type custom table |
| Google Chronicle | `ChronicleForwarder` | JWT-minted OAuth2 token     | UDM mapping, token caching |

Each forwarder serialises an `audit_logs` row as JSON, retries on 5xx
with exponential backoff, and surfaces failures as
`acp_siem_forwarder_failures_total{target=<name>}` so the AlertManager
rule `SiemForwarderUnhealthy` fires on sustained outage.

**Credentials** are pulled from env first, then `/aegis-siem/{target}/...`
SSM Parameter Store keys — the same convention every other Aegis
secret follows. See `scripts/ops/run_e2e.sh` for the SSM pattern.

---

## 2 · OpenTelemetry decision exporter (`sdk/common/otel_exporter.py`)

Sprint 3 emits vendor-neutral OTel spans for every `/execute` decision —
one root span (`aegis.decision`) plus one child per pipeline stage
(`aegis.stage.auth`, `aegis.stage.policy`, …) with GenAI semantic
conventions on every attribute (`gen_ai.usage.input_tokens`,
`gen_ai.usage.cost`, etc.).

Sprint 8 ships the EXPORTER that ships those spans to your existing
backend. The exporter is environment-driven so it slots into any
deployment surface (compose, Kubernetes, ECS, plain EC2):

```bash
export AEGIS_OTEL_EXPORTER_ENABLED=true
export AEGIS_OTEL_EXPORTER_PROTOCOL=http/protobuf
export AEGIS_OTEL_EXPORTER_ENDPOINT=<see-below>
export AEGIS_OTEL_EXPORTER_HEADERS=<see-below>
export AEGIS_OTEL_SERVICE_NAME=aegis-gateway
```

The exporter is a no-op when the enabled flag is unset, so the same
process boots cleanly with or without the env wiring.

### Backend recipes

**Datadog (US1):**
```
AEGIS_OTEL_EXPORTER_ENDPOINT=https://api.datadoghq.com
AEGIS_OTEL_EXPORTER_HEADERS=DD-API-KEY=<your-key>
```

**Grafana Cloud / Tempo (OTLP HTTP):**
```
AEGIS_OTEL_EXPORTER_ENDPOINT=https://tempo-prod-XX-prod-eu-west-2.grafana.net/otlp
AEGIS_OTEL_EXPORTER_HEADERS=Authorization=Basic <base64-encoded-token>
```

**Amazon CloudWatch GenAI Observability** (via the AWS Distro OTel
Collector — ADOT runs as a sidecar / DaemonSet and handles SigV4 to
the CloudWatch backend):
```
AEGIS_OTEL_EXPORTER_ENDPOINT=http://adot-collector:4318
# No headers — the ADOT collector handles AWS auth.
```

**Honeycomb / generic OTLP HTTP:**
```
AEGIS_OTEL_EXPORTER_ENDPOINT=https://api.honeycomb.io
AEGIS_OTEL_EXPORTER_HEADERS=x-honeycomb-team=<key>
```

### Verification

Once the exporter is wired:

1. Trigger one `/execute` call against the gateway.
2. The `aegis.decision` span should appear in your backend within a
   few seconds (the `BatchSpanProcessor` flushes every
   `AEGIS_OTEL_BATCH_DELAY_MS` — 5 s by default).
3. Confirm the child stages — `aegis.stage.auth`, `aegis.stage.policy`,
   etc. — render under the same trace id.

If nothing arrives, check `aegis-gateway` logs for either
`otel_exporter_install_failed` (bad endpoint / wheel missing) or
`otel_exporter_missing_endpoint` (env var was empty).

---

## 3 · MCP server (`services/mcp_server/`)

Aegis exposes four governance tools over the Model Context Protocol so
any MCP-aware client (Claude Desktop, Cursor, the Sprint-8 VS Code
extension) can wire Aegis decisions directly into the agent loop —
*before* a destructive action runs.

| Tool                       | What it returns |
|----------------------------|-----------------|
| `aegis.evaluate_action`    | Live `/execute` decision (allow/deny/throttle/escalate), risk, findings, receipt id |
| `aegis.fetch_receipt`      | Signed receipt for a past execution (ed25519, canonical JSON, `kid`) |
| `aegis.verify_chain`       | Streams the audit chain over a window; detects truncation + tampering |
| `aegis.query_blast_radius` | Identity-graph BFS from an `agent_id` — "if THIS agent is compromised, what does it reach?" |

Run:

```bash
AEGIS_GATEWAY_URL=https://ha.aegisagent.in \
AEGIS_MCP_API_KEY=<aegis-api-key> \
python -m services.mcp_server
```

Or wire into Claude Desktop's `mcpServers` config:

```json
{
  "mcpServers": {
    "aegis": {
      "command": "python",
      "args": ["-m", "services.mcp_server"],
      "env": {
        "AEGIS_GATEWAY_URL": "https://ha.aegisagent.in",
        "AEGIS_MCP_API_KEY": "<aegis-api-key>"
      }
    }
  }
}
```

The API key is validated against `POST /api-keys/validate` once at
tool invocation; the returned `tenant_id` becomes the `X-Tenant-ID`
header on every downstream call. Tenants cannot spoof another tenant.

---

## 4 · VS Code extension (`vscode-extension/`)

Read-only sidebar showing the last N `/execute` decisions for the
developer's tenant, with click-through to the **signed receipt
webview** (ed25519 signature, canonical-JSON sha256, signing-key id).

Install:

```bash
cd vscode-extension
npm install
npm run compile
# In VS Code: F5 launches the Extension Development Host
```

The API key is stored in **VS Code SecretStorage** (OS keychain on
macOS / Linux secret service / Windows credential vault) — never in
`settings.json`, never in sync.

---

## 5 · Where each evidence stream goes

| Use case                                       | Channel              |
|------------------------------------------------|----------------------|
| Long-term compliance archive (EU AI Act / SOC 2) | SIEM forwarders     |
| Per-decision distributed tracing (latency, FinOps) | OTel exporter      |
| Inline policy enforcement inside an agent loop | MCP server          |
| Developer-facing decision visibility           | VS Code extension    |
| Offline cryptographic re-verification          | `acp verify-chain` CLI (and `aegis.verify_chain` MCP tool) |
| In-product dashboards                          | Aegis UI (Fleet, Shadow Mode, Playground, Evaluation) |

The same `audit_logs` row anchors all of them — the SIEM forwarder
sees the same canonical JSON the receipt is signed over, which is the
same data the MCP server returns. There is no separate "evidence
pipeline"; there is one chain, and these are the windows onto it.

---

## 5 · AEVF back-reference in every SIEM record (A6)

As of 2026-06-14, every event forwarded to Splunk / Datadog / Elastic /
Sentinel / Chronicle carries three additional fields:

```jsonc
{
  // ... existing SIEM fields ...
  "aevf_bundle_url":   "https://ha.aegisagent.in/compliance/export/eu-ai-act?period_start=…&period_end=…",
  "aevf_event_hash":   "<sha256 hex>",
  "aevf_spec_version": "aevf/0.1.0"
}
```

The fields are populated by `SIEMEvent.from_audit_log()` in
`services/audit/siem.py`. The bundle URL is the day-bundle that
contains the same row; the auditor follows it, downloads the AEVF
bundle, and verifies the same row offline.

`AEVF_BASE_URL` is configurable via `AEVF_PUBLIC_BASE_URL` env var
(default `https://ha.aegisagent.in`) so self-host customers can pin
their own host.

In Splunk, a saved search that exposes a clickable verify link:

```spl
| eval verify = "<a href=" . aevf_bundle_url . " target=_blank>verify offline</a>"
```

## 6 · GRC evidence export (A6)

New endpoint: `GET /compliance/export/grc?format=json|csv` — Vanta /
Drata / Secureframe / Hyperproof style control-evidence export. Each
evidence row pairs an audit row with each `(framework, control_id)` it
maps to.

> **Live on prod-ha 2026-06-14**: 40/40 hits (20 json + 20 csv) returned
> HTTP 200/206 against `https://ha.aegisagent.in`. JSON variant for a
> 18-month window returns ~12 MB; CSV variant ~8 MB. The endpoint went
> 400 → 200 the morning of 2026-06-14 when the stale audit service
> snapshot (which excluded `grc` from its `_VALID` bundle-type set) was
> redeployed; see [Deployment](../operations/deployment.md).

Implementation: `services/audit/grc_export.py`. Schema:

| Column | Notes |
|---|---|
| `evidence_id` | Stable `sha256(control_id || event_hash)[:32]` |
| `evidence_type` | Always `"automated_control_test"` |
| `control_framework` | `SOC2 \| EU_AI_ACT \| NIST_AI_RMF \| DPDP` |
| `control_id` | Framework-native control identifier, e.g. `CC6.1`, `Article 12`, `Section 8(5)` |
| `collected_at` | ISO-8601 timestamp of the underlying audit row |
| `tenant_id`, `agent_id`, `action`, `tool`, `decision` | Verbatim from the audit row |
| `summary` | Human-readable sentence the GRC platform shows the auditor |
| **`aevf_bundle_url`** | Day-bundle URL for the row's framework |
| **`aevf_event_hash`** | Locator inside the AEVF bundle |
| **`aevf_spec_version`** | `aevf/0.1.0` |

CSV variant is RFC 4180 + UTF-8; JSON variant wraps the rows in an
envelope:

```jsonc
{
  "format":            "aegis-grc-export/2026-06",
  "aevf_spec_version": "aevf/0.1.0",
  "tenant_id":         "...",
  "period":            { "start": "...", "end": "..." },
  "generated_at":      "...",
  "evidence":          [ /* the rows */ ]
}
```

A buyer using Vanta drops the CSV into their evidence inbox; Vanta
correlates `control_id` to its control inventory and shows "evidence
collected on 2026-06-13" with a clickable `aevf_bundle_url` next to it.
The auditor clicks, downloads the AEVF bundle, runs `aegis-verify`,
and gets PASS without ever talking to us.

## 7 · README parity

This page is the source of truth. The `README.md` integrations section
lists each channel by name and links HERE for the recipe. If a future
sprint adds a backend (e.g. Splunk OnPrem, a new SIEM target,
ServiceNow), it MUST land in §1–§4 here in the same PR that ships the
code. The CI lint at `scripts/dev/check_evidence_export_parity.sh`
(Sprint 8 follow-up) enforces this.
