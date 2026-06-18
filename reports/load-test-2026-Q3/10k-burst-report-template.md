# Aegis v2.0 — 10 000-VU burst load test (Track D2)

> **Status:** template — populate after running per `README.md`.

---

## Run metadata

| Field                    | Value                                              |
|--------------------------|----------------------------------------------------|
| Date / time (UTC)        | `<YYYY-MM-DD HH:MM>`                               |
| Target                   | `https://ha.aegisagent.in`                         |
| Shape                    | 100 → 10 000 VUs over 60 s · hold 5 min · ramp down |
| Total wall-clock         | ~ 7 minutes including cooldown                     |
| Tenants                  | 5                                                  |
| Load generator           | 4-node Locust cluster, `<region/instance type>`    |
| Aegis build              | `<git sha>` (tag `v2.0-pre-deploy` rebased to ALB) |
| Operator                 | `<name>`                                           |

---

## Pass criteria

| Criterion                                                       | Pass line                | Result   |
|-----------------------------------------------------------------|--------------------------|----------|
| p95 latency during the burst (5-minute hold window)             | < 1 500 ms               | ✅ / ❌    |
| No 5xx storm — shed-load engages cleanly                        | no `5xx > 1% > 30 s` block | ✅ / ❌    |
| Behavior firewall stays available                               | zero `behavior_service_unavailable` audit rows | ✅ / ❌ |
| p95 returns to D1 baseline after ramp-down                      | within 90 s              | ✅ / ❌    |
| Audit-chain verification after the run                          | no violation             | ✅ / ❌    |

A failure on any row blocks publication.

---

## Headline numbers

### Burst-window (peak 10 000 VUs, 5-minute hold)

| Metric                    | Measured |
|---------------------------|---------:|
| p50 latency               | `<ms>`   |
| p95 latency               | `<ms>`   |
| p99 latency               | `<ms>`   |
| Error rate                | `<%>`    |
| Peak throughput (req/s)   | `<N>`    |
| 5xx count                 | `<N>`    |
| Shed-load 429 count       | `<N>`    |

### Recovery window (first 90 seconds after ramp-down ends)

| Metric                  | Measured | Baseline (D1) | Within +20 %? |
|-------------------------|---------:|--------------:|---------------|
| p95 latency             | `<ms>`   | `<ms>`        | ✅ / ❌         |
| Error rate              | `<%>`    | `<%>`         | ✅ / ❌         |

---

## Behavior firewall audit

(Pulled from `audit_logs` over the burst window.)

| Action                             | Count |
|------------------------------------|------:|
| `behavior_service_unavailable`     | `<N>` |
| `behavior_firewall_consult_total`  | `<N>` |
| `behavior_firewall_consult_p99_ms` | `<N>` |

If `behavior_service_unavailable` is non-zero, document each event in
"Anomalies" — per-tenant `degraded_mode_policy` should fire cleanly and
not surface a customer-side 5xx.

---

## Shed-load behaviour

| Component             | Observed during burst         |
|-----------------------|-------------------------------|
| Gateway 429 emission  | `<count, %>`                  |
| Tenant rate-limiter   | `<which tenants tripped>`     |
| Behavior firewall     | `<degraded mode? which policy?>` |
| OPA evaluation        | `<budget cap hit? count>`     |

Shed-load is a *passing* outcome — graceful degradation is the design.
The failure mode is uncontrolled 5xx, not the controlled 429.

---

## Post-run validation

(From `10k-burst/checks.json`.)

| Check                                     | Result   | Notes                                  |
|-------------------------------------------|----------|----------------------------------------|
| Audit-chain verification (`aegis verify-chain`) | ✅ / ❌  |                                        |
| Reconciliation (`scripts/ops/reconcile.py`)     | ✅ / ❌  |                                        |
| Flight-recorder timelines closed                | ✅ / ❌  |                                        |
| Transparency roots verified                     | ✅ / ❌  |                                        |

---

## Anomalies / regressions

Anything unexpected during the burst or recovery: a per-tenant spike,
an extended 429 plateau, a brief Grafana gap, etc.

---

## Action items

| ID    | Action                              | Owner   | Due       | Status      |
|-------|-------------------------------------|---------|-----------|-------------|
| LT-1  | (e.g. raise OPA budget cap)         | `<name>`| `<date>`  | open/closed |

---

## Artefacts

- Locust CSVs and JSON summaries under `10k-burst/`.
- Grafana snapshot — `<URL>` — captured for the burst + recovery window.
- PagerDuty events during the run (anything that paged): `<list>`.

---

## Sign-off

| Role            | Name      | Date      |
|-----------------|-----------|-----------|
| Test operator   | `<name>`  | `<date>`  |
| SRE on-call     | `<name>`  | `<date>`  |
| Engineering lead| `<name>`  | `<date>`  |
