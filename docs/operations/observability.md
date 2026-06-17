# Observability

*Prometheus, Grafana, Jaeger, Alertmanager — what is collected, where it lives, how to reach it, and what to watch.*

## The stack

Source: `infra/docker-compose.yml` (observability containers) and `infra/prometheus/`.

| Component | Container | Purpose |
|---|---|---|
| Prometheus | `acp_prometheus` (`prom/prometheus:v2.55.1`) | Scrapes `/metrics` on every service; evaluates alert rules |
| Alertmanager | `acp_alertmanager` (`prom/alertmanager:v0.27.0`) | Routes alerts to Slack and PagerDuty |
| Grafana | `acp_grafana` (`grafana/grafana:11.3.0`) | Dashboards |
| Jaeger | `acp_jaeger` (`jaegertracing/all-in-one:1.57`) | OpenTelemetry trace collector |

All four run alongside the application services on each EC2. They do not participate in the request path and can be restarted independently.

## Reaching them

The observability stack is not exposed publicly via `https://aegisagent.in`. Access is via SSH port-forward to either ASG host:

```bash
ssh -i <path-to-your-ec2-key.pem> ubuntu@<ec2-ip> \
  -L 3000:localhost:3000 \   # Grafana
  -L 9090:localhost:9090 \   # Prometheus
  -L 16686:localhost:16686 \ # Jaeger
  -L 9093:localhost:9093     # Alertmanager
```

Then browse to `localhost:3000` (etc.) on the laptop.

Every service exposes `/metrics` via `prometheus_fastapi_instrumentator`. Prometheus scrape interval is 15 seconds across the static-config target list.

## Dashboards

Five built-in Grafana dashboards under `infra/grafana-dashboards/`. Import them via Grafana → Dashboards → Import → paste JSON. They are operator-only; customer-facing tiles live in the Aegis UI.

### `acp-platform-slo.json` — Platform SLO

The most-used dashboard. Tracks the SLOs operators are paged on:

- `/execute` p50 / p95 / p99 latency.
- Request rate (req/s by status).
- Error rate as a percent of total.
- Rate-limited requests per tenant by limit_type.
- Decision-pipeline behavior-consult p95 / p99.

Anyone debugging a slow request opens this first.

### `acp-trust-layers.json` — Trust Layers

Chain-integrity and trust-pipeline health. The most consequential tiles:

- **Chain integrity violations** — must stay at 0. Any non-zero value pages.
- Reconciliation gap (`audit ↔ usage`) per tenant.
- Behavior firewall consult outcome distribution.
- Transparency-root seal lag (seconds since the last successful seal).
- Transparency roots committed (cumulative).
- Flight-timeline close lag (`open_total − closed_total`).
- Behavior consult skipped / timeout / error rates.

Open this after any `ChainViolationImmediate` page.

### `acp-tenant-activity.json` — Tenant Activity

Per-tenant aggregates with a tenant template variable:

- Per-tenant request rate.
- Per-tenant rate-limited by limit_type.
- Per-tenant daily inference $ used.
- Inference-cost-blocked by scope (tenant vs. agent).
- Per-tenant monthly-quota 80% warning fires.

Useful when a customer reports a problem — pick the tenant, drill.

### `acp-queues.json` — Queues

Backpressure indicators:

- `acp:audit_events` stream length + dropped/s.
- Audit DLQ + Billing DLQ oldest-age.
- Outbox pending count + oldest-pending age per outbox.
- Insight / Groq queue depth + oldest age.
- Flight Recorder leaked timelines (in_progress > 60s).
- Reconciliation outbox-age per tenant.

When the platform feels slow, queues are usually the cause.

### `acp-operations.json` — Operations

Cross-service request / outbox / billing operational view:

- Request throughput by service.
- p95 latency per service.
- Outbox depth (pending vs. poison).
- Audit stream length + drop rate.
- Billing pipeline events + outbox processed/retry.
- Behavior fail-closed rate.
- `/status` (gateway_internal) vs. `/system/health` (end_to_end) p95 / p99.

## Alerts

Source: `infra/prometheus-rules.yml`.

Alertmanager (`infra/alertmanager.yml`) routes by severity label:

| Severity label | Receiver | group_wait | repeat_interval | Channels |
|---|---|---|---|---|
| `page` | `critical` | **0s** (no grouping delay) | 30m | Slack `#aegis-critical` + PagerDuty |
| `critical` | `critical` | 10s | 30m | Slack `#aegis-critical` + PagerDuty |
| `warning` (default fallthrough) | `slack` | 30s | 4h | Slack `#aegis-alerts` |

The `severity=page` route is reserved for alerts that must wake an operator immediately. Today only **`ChainViolationImmediate`** carries that label — audit-chain corruption is the one condition for which a 30-second group wait is too long. The route fires the moment the metric crosses the threshold (`for: 0m`) and repeats until acknowledged.

| Alert | Severity | Trigger | Runbook |
|---|---|---|---|
| `ChainViolationImmediate` | **page** | `acp_audit_chain_violations_total > 0` (`for: 0m`) | [audit-chain-violation](runbooks/audit-chain-violation.md) |
| `ServiceUnavailable` | critical | `up{job="acp-services"} == 0` for 2m | n/a |
| `OutboxPoisonGrowing` | critical | `increase(acp_outbox_poison_total[5m]) > 0` | n/a |
| `ReconciliationAuditWithoutUsage` | critical | `acp_reconcile_audit_without_usage > 0` for 5m | n/a |
| `ReconciliationUsageWithoutAudit` | critical | `acp_reconcile_usage_without_audit > 0` for 5m | n/a |
| `ReconciliationGapSustained` | critical | Either reconciliation gap unresolved for 15m | n/a |
| `OutboxOldestPendingAgeHigh` | critical | `acp_outbox_oldest_pending_age_seconds > 60` for 5m | n/a |
| `AuditDLQGrowing` | critical | `acp_audit_dlq_oldest_age_seconds > 60` for 5m | n/a |
| `BillingDLQGrowing` | critical | `acp_billing_dlq_oldest_age_seconds > 60` for 5m | n/a |
| `BillingDLQNonZero` | warning | `acp_audit_stream_length > 45000` for 5m | n/a |
| `BehaviorFailClosedSustained` | warning | fail-closed rate > 5% for 3m | n/a |
| `P95LatencyBudgetBreach` | warning | p95 latency > 400ms for 5m | n/a |
| `AuthFailureSpike` | warning | duplicate-drops rate elevated | [rate-limit-spike](runbooks/rate-limit-spike.md) |
| `InsightQueueAgeHigh` | warning | `acp_insight_queue_oldest_age_seconds > 60` for 5m | n/a |
| `FlightTimelineLeak` | warning | `acp_flight_timeline_in_progress_count > 10` for 5m | n/a |
| `InferenceCostCapBlocking` | warning | `rate(acp_inference_cost_blocked_total[5m]) > 0` for 5m | n/a |
| `HighMTTR` | warning | `acp_incident_mttr_seconds > 3600` for 5m | n/a |
| `DailyBudgetWarning` | warning | `acp_tenant_daily_cost_usd > 5` | n/a |
| `HighIncidentRate` | warning | `>10` new incidents per hour | n/a |

The runbook column points at the operator response — only the highest-impact alerts have dedicated runbooks. Everything else is investigated via Grafana + audit-log search.

PagerDuty wiring is operator-side: drop the service routing key into `/etc/alertmanager/pagerduty_routing_key` on each ASG host. Without it the page route still posts to Slack but PagerDuty delivery silently no-ops.

## Tracing

Every gateway request becomes one OpenTelemetry trace with the 11 stages as spans. The trace carries the request_id which is also on the audit row, so a trace can be correlated with the durable audit record.

To find a trace by request_id:

1. Open Jaeger UI (via the SSH tunnel).
2. Service = `gateway`, Tag = `request_id=<uuid>`.
3. The trace shows per-stage latency and any errors.

Useful for "why did this one request take 800 ms" debugging.

### OTel exporter to your own backend (Sprint 8)

In addition to the on-host Jaeger UI, the gateway can ship the same `aegis.decision` traces to any OTLP-compatible backend (Datadog, Grafana Cloud / Tempo, Honeycomb, Amazon CloudWatch GenAI Observability via ADOT, etc.). Source: `sdk/common/otel_exporter.py`. The exporter is environment-driven and a no-op when the enabled flag is unset:

```bash
export AEGIS_OTEL_EXPORTER_ENABLED=true
export AEGIS_OTEL_EXPORTER_PROTOCOL=http/protobuf
export AEGIS_OTEL_EXPORTER_ENDPOINT=<vendor-specific>
export AEGIS_OTEL_EXPORTER_HEADERS=<vendor-specific>
export AEGIS_OTEL_SERVICE_NAME=aegis-gateway
```

Full backend recipes (Datadog US1, Grafana Cloud Tempo, ADOT collector for CloudWatch, Honeycomb / generic OTLP) live in [Evidence Export Adapters](../integrations/evidence-export.md#2--opentelemetry-decision-exporter-sdkcommonotel_exporterpy).

## The `/metrics` endpoint

Every service exposes Prometheus metrics at `/metrics`. The format is the standard Prometheus exposition format. Scrape interval is 15 seconds.

Metric naming conventions (from the services pages in this docs):

- `acp_<service>_<metric>_total` — counters.
- `acp_<service>_<metric>_seconds` — histograms of duration.
- `acp_<service>_<metric>_<resource>_size` — gauges of resource consumption.

Labels: every metric carries `tenant_id` where applicable. Per-tenant aggregation is the default.

## Custom queries

PromQL examples that operators actually use:

```promql
# Top 5 tenants by decision rate
topk(5, sum by (tenant_id) (rate(acp_gateway_request_total[5m])))

# Per-stage p95 latency
histogram_quantile(0.95, sum by (le, stage) (rate(acp_gateway_stage_latency_seconds_bucket[5m])))

# Audit chain growth rate
rate(acp_audit_logs_written_total[5m])

# Behavior firewall denies per tenant
sum by (tenant_id) (rate(acp_behavior_firewall_consult_total{result="deny"}[5m]))

# Outbox depth (should be near zero)
acp_audit_outbox_oldest_age_seconds

# Circuit breaker state across services
sum by (target_service) (acp_gateway_circuit_breaker_open)
```

## Long-term storage

Prometheus retains 15 days locally. Long-term metric storage is not configured in the demo deployment; production deployments would add Thanos or VictoriaMetrics for years-long retention.

Jaeger retains 24 hours of traces. For longer trace retention, configure an external storage backend.

Audit chain data lives in Postgres and S3 (receipts). The chain itself is the long-term durable record; observability metrics are operational.

## Observability hygiene

A few things to verify periodically:

1. **Every service is being scraped.** Grafana → Explore → query `up`. Every job should be 1.
2. **No metric cardinality explosion.** A single metric with many labels can balloon Prometheus memory. Watch for `prometheus_tsdb_head_series` growth.
3. **Alerts are firing where expected.** Test alerts by triggering known conditions; verify Slack and PagerDuty receive them.
4. **Dashboards load fast.** A slow dashboard often means a slow PromQL — refactor with recording rules.

## What this stack does NOT include

- **APM-style request tracking across user sessions.** Aegis traces individual requests, not user journeys. Customer-side analytics would be a separate concern.
- **Log aggregation.** Container stdout is captured by the local Docker logging driver. For centralized logging, configure a SIEM forwarder via Settings → SIEM. As of 2026-06-14, every SIEM event also carries `aevf_bundle_url` / `aevf_event_hash` / `aevf_spec_version` so the auditor can pivot from a SIEM row to the verifiable bundle — see [SIEM Forwarders](siem-forwarders.md#aevf-back-reference-fields-a6-2026-06-14).
- **Anomaly detection on metrics.** Alert rules are static thresholds. ML-based anomaly detection is roadmap.
- **Customer-facing dashboards.** The Grafana dashboards are operator-only. Customer-facing dashboards live in the Aegis UI (Observability, Risk Engine, Billing).

## Next

- [Deployment Topology](../architecture/deployment-topology.md) — where the observability containers run on each of the two ASG hosts
- [Evidence Export Adapters](../integrations/evidence-export.md) — every channel audit evidence exits through (SIEM, OTel, MCP, VS Code, GRC)
- [Gateway service](../services/gateway.md) — most-instrumented service
- [Audit service](../services/audit.md) — owns the chain-integrity metrics
- [SIEM Forwarders](siem-forwarders.md) — Splunk / Datadog / Elastic / Sentinel / Chronicle with AEVF back-reference
- [Audit Chain Violation runbook](runbooks/audit-chain-violation.md) — the page-tier alert
- [Kill Switch Engaged runbook](runbooks/kill-switch-engaged.md) — when a tenant is intentionally halted
- [Rate Limit Spike runbook](runbooks/rate-limit-spike.md) — when 429s or auth-fails surge
- [Anthropic Key Rotation runbook](runbooks/anthropic-key-rotation.md) — rotating `ANTHROPIC_API_KEY` for the gateway LLM router
