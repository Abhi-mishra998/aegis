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

The observability stack is not exposed publicly. Access is via SSH port-forward:

```bash
ssh -i <path-to-your-ec2-key.pem> ubuntu@<ec2-ip> \
  -L 3000:localhost:3000 \   # Grafana
  -L 9090:localhost:9090 \   # Prometheus
  -L 16686:localhost:16686 \ # Jaeger
  -L 9093:localhost:9093     # Alertmanager
```

Then browse to `localhost:3000` (etc.) on the laptop.

## Dashboards

Four built-in Grafana dashboards under `infra/grafana-dashboards/`:

### `platform_slo.json` — Platform SLO

The most-used dashboard. Top-of-page tiles:

- p50 / p95 / p99 gateway latency.
- Decisions per second (allow + deny + escalate).
- Audit outbox depth and age.
- Service availability per service.

Per-service rows show the same stats per microservice. Anyone debugging a slow request opens this first.

### `trust_layers.json` — Trust Layers

Per-stage breakdown of the gateway pipeline:

- Stage 0–10 latency histograms.
- Per-stage deny counts.
- Behavior-firewall consult result distribution.
- Per-stage skipped invocations.

Operators tuning a specific stage open this.

### `tenant_activity.json` — Tenant Activity

Per-tenant aggregates:

- Decisions per minute per tenant.
- Per-tenant latency.
- Per-tenant cost spend.
- Per-tenant cap utilization.

Useful when a customer reports a problem — drill to the specific tenant.

### `queues.json` — Queues

Backpressure indicators:

- `acp:audit_events` stream depth.
- `acp:groq_events` stream depth.
- `pending_usage_events` table depth.
- DLQ sizes.
- Per-worker consumer-group lag.

When the platform feels slow, queues are usually the cause.

## Alerts

Source: `infra/prometheus/alert.rules.yml`.

| Alert | Severity | Trigger | Runbook |
|---|---|---|---|
| `ChainViolationImmediate` | P0 | `acp_audit_chain_violations_total > 0` | [audit-chain-violation](runbooks/audit-chain-violation.md) |
| `AuditOutboxStuck` | P1 | `acp_audit_outbox_oldest_age_seconds > 300` for 5m | [audit-chain-violation](runbooks/audit-chain-violation.md) |
| `GatewayLatencyHigh` | P2 | gateway p95 > 500ms for 10m | n/a |
| `KillSwitchEngaged` | P1 (informational) | kill-switch state changes | [kill-switch-engaged](runbooks/kill-switch-engaged.md) |
| `RateLimitSpike` | P2 | `acp:auth_fail:*` counters elevated | [rate-limit-spike](runbooks/rate-limit-spike.md) |
| `BillingReconcileGap` | P1 | `acp_reconcile_audit_without_usage > 0` for 10m | n/a |
| `CircuitBreakerOpen` | P1 | `acp_gateway_circuit_breaker_open == 1` for 5m | n/a |
| `BehaviorServiceDegraded` | P2 | behavior consult error rate > 10% for 10m | n/a |

The runbook column points at the operator response.

## Tracing

Every gateway request becomes one OpenTelemetry trace with the 11 stages as spans. The trace carries the request_id which is also on the audit row, so a trace can be correlated with the durable audit record.

To find a trace by request_id:

1. Open Jaeger UI (via the SSH tunnel).
2. Service = `gateway`, Tag = `request_id=<uuid>`.
3. The trace shows per-stage latency and any errors.

Useful for "why did this one request take 800 ms" debugging.

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
- **Log aggregation.** Container stdout is captured by the local Docker logging driver. For centralized logging, configure a SIEM forwarder via Settings → SIEM.
- **Anomaly detection on metrics.** Alert rules are static thresholds. ML-based anomaly detection is roadmap.
- **Customer-facing dashboards.** The Grafana dashboards are operator-only. Customer-facing dashboards live in the Aegis UI (Observability, Risk Engine, Billing).

## Next

- [Deployment Topology](../architecture/deployment-topology.md) — where the observability containers run
- [Gateway service](../services/gateway.md) — most-instrumented service
- [Audit service](../services/audit.md) — owns the chain-integrity metrics
- [Audit Chain Violation runbook](runbooks/audit-chain-violation.md) — the alert that matters most
