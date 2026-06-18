# Aegis v2.0 — 1k RPS sustained load test (Track D1)

> **Status:** template — populate after running per `README.md`.

---

## Run metadata

| Field                    | Value                                              |
|--------------------------|----------------------------------------------------|
| Date / time (UTC)        | `<YYYY-MM-DD HH:MM>`                               |
| Target                   | `https://ha.aegisagent.in`                         |
| Duration                 | 30 minutes (steady-state)                          |
| Sustained users          | 1 000                                              |
| Tenants                  | 5                                                  |
| Load generator           | 4-node Locust cluster, `<region/instance type>`    |
| Aegis build              | `<git sha>` (tag `v2.0-pre-deploy` rebased to ALB) |
| Operator                 | `<name>`                                           |

---

## Headline numbers

| Metric                     | Measured | Pass line | Result   |
|----------------------------|---------:|----------:|----------|
| p50 latency                | `<ms>`   | 100 ms    | ✅ / ❌    |
| p95 latency                | `<ms>`   | 500 ms    | ✅ / ❌    |
| p99 latency                | `<ms>`   | 1 500 ms  | ✅ / ❌    |
| Error rate                 | `<%>`    | 0.5 %     | ✅ / ❌    |
| Total requests             | `<N>`    | —         | —        |
| Total failures             | `<N>`    | —         | —        |
| Throughput (rps, average)  | `<N>`    | ≥ 950 rps | ✅ / ❌    |

---

## Per-endpoint breakdown

(From `1k-rps/locust_stats.csv` — render as a table here.)

| Endpoint                | Count | p50 | p95 | p99 | Failures |
|-------------------------|------:|----:|----:|----:|---------:|
| `/execute/legit`        |       |     |     |     |          |
| `/policies/PUT`         |       |     |     |     |          |
| `/execute/after-policy` |       |     |     |     |          |
| `/logs`                 |       |     |     |     |          |
| `/events/stream`        |       |     |     |     |          |
| `/agents`               |       |     |     |     |          |
| `/status`               |       |     |     |     |          |

---

## Per-tenant fairness

(From the `|tenant=<id>` suffix in the locust stat rows.)

| Tenant | p50 | p95 | p99 | Δ from baseline p99 |
|--------|----:|----:|----:|--------------------:|
|        |     |     |     |                     |

Pass line: quiet tenant p99 must be within +20 % of baseline.

---

## Post-run validation

(From `1k-rps/checks.json`. Pulled in by the orchestrator after locust exits.)

| Check                                     | Result   | Notes                                  |
|-------------------------------------------|----------|----------------------------------------|
| Audit-chain verification (`aegis verify-chain`) | ✅ / ❌  |                                        |
| Reconciliation (`scripts/ops/reconcile.py`)     | ✅ / ❌  |                                        |
| Flight-recorder timelines closed                | ✅ / ❌  |                                        |
| Transparency roots verified                     | ✅ / ❌  |                                        |

---

## Anomalies / regressions

Free-form section. Note anything unexpected: a single endpoint that
ran hot, a tenant whose p99 spiked, a brief 5xx surge that did not
breach the threshold but is worth a follow-up.

---

## Action items

| ID    | Action                              | Owner   | Due       | Status      |
|-------|-------------------------------------|---------|-----------|-------------|
| LT-1  | (e.g. revisit OPA budget cap)       | `<name>`| `<date>`  | open/closed |

---

## Artefacts

- Locust CSVs and JSON summaries under `1k-rps/`.
- Grafana snapshot — `<URL>` — captured for the steady-state window.
- PagerDuty quiet period during the run (no false pages): `<true/false>`.

---

## Sign-off

| Role            | Name      | Date      |
|-----------------|-----------|-----------|
| Test operator   | `<name>`  | `<date>`  |
| SRE on-call     | `<name>`  | `<date>`  |
| Engineering lead| `<name>`  | `<date>`  |
