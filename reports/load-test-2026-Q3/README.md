# Aegis v2.0 — Production load-test evidence (2026-Q3)

This directory holds the published load-test evidence for the v2.0 GA
release. Two reports are owed:

- `1k-rps-report.md` — sustained 1k RPS for 30 minutes (Track D1).
- `10k-burst-report.md` — burst from 100 → 10 000 VUs over 60 s, hold 5 minutes (Track D2).

The methodology is documented here; the actual measured numbers land in
the per-report files once the SRE team runs the test. Until then,
`agies-bussiness.md` §12 cites only the synthetic dry-run from
`reports/gateway_p95_dry.json` (21.49 ms p95, single host, 4 concurrency).

---

## Methodology

### Target

- HA endpoint: `https://ha.aegisagent.in/v1/messages` and `/execute/<tool>`.
- Five test tenants provisioned by `tests/load/soak.py` (the orchestrator
  also handles JWT minting and per-tenant token rotation).
- Load generator: four-node Locust cluster co-located with the prod ALB
  region (`ap-south-1`) to keep the LG → ALB RTT under 5 ms.

### Traffic mix (D1 sustained)

Codified in `tests/load/v2_realistic_user.py` (a sibling of `soak_user.py`).
The realistic mix is intentionally different from the soak-harness
attack-shaped mix:

| Weight | Endpoint                              | What it measures                            |
|-------:|---------------------------------------|---------------------------------------------|
| 60     | `POST /execute/<tool>` (legit)        | Customer-facing decision latency on allow.  |
| 15     | `PUT /policies/{id}` + `POST /execute`| Policy-bundle round-trip + cache-miss path. |
| 10     | `GET /logs`                           | Audit-query SLA under sustained read load.  |
| 10     | `GET /events/stream`                  | SSE dispatcher behaviour under fanout.      |
|  5     | `GET /agents` or `GET /status`        | Admin-surface availability under load.      |

### Run command (D1)

```bash
# From repo root, with INTERNAL_SECRET + GATEWAY_URL in env.
GATEWAY_URL=https://ha.aegisagent.in \
INTERNAL_SECRET=$ACP_PROD_INTERNAL_SECRET \
python tests/load/soak.py \
    --user-class v2_realistic_user.V2RealisticUser \
    --users 1000 --duration 30m --tenants 5 \
    --report-dir reports/load-test-2026-Q3/1k-rps
```

The orchestrator writes locust CSVs, `summary.json`, `manifest.json`, and
`checks.json` into the report directory. The SRE then renders
`1k-rps-report.md` from those artefacts using the template below.

### Run command (D2 burst)

```bash
# 100 -> 10000 VUs over 60s, hold 5min, ramp down.
# Uses the same user file with a different shape.
GATEWAY_URL=https://ha.aegisagent.in \
INTERNAL_SECRET=$ACP_PROD_INTERNAL_SECRET \
python tests/load/soak.py \
    --user-class v2_realistic_user.V2RealisticUser \
    --shape burst_10k --duration 7m --tenants 5 \
    --report-dir reports/load-test-2026-Q3/10k-burst
```

`burst_10k` is the locust step-load shape: 60 s ramp to 10 000 → 5 min
hold → 60 s ramp down.

---

## Pass criteria

### D1 — sustained 1k RPS / 30 minutes

| Metric                                  | Pass line             |
|-----------------------------------------|-----------------------|
| p50 latency                             | < 100 ms              |
| p95 latency                             | < 500 ms              |
| p99 latency                             | < 1 500 ms            |
| Error rate                              | < 0.5 %               |
| Audit chain verification after the run  | no violation          |
| Per-tenant fairness (quiet tenant p99)  | within +20 % of baseline |

### D2 — 10k burst / 5-minute hold

| Metric                                                   | Pass line             |
|----------------------------------------------------------|-----------------------|
| p95 latency during the burst window                      | < 1 500 ms            |
| 5xx storm                                                | none                  |
| Behavior firewall available (no `behavior_service_unavailable` audit rows) | yes |
| Recovery — p95 returns to D1 baseline                    | within 90 s           |

A failure in any row blocks the publication of the report. The fix
lands in the next sprint, not the report.

---

## Output layout

After a run, the per-test directory looks like:

```
reports/load-test-2026-Q3/
├── README.md                          (this file)
├── 1k-rps-report.md                   (rendered by SRE; template alongside)
├── 1k-rps-report-template.md          (skeleton)
├── 1k-rps/
│   ├── locust_stats.csv
│   ├── locust_stats_history.csv
│   ├── locust_failures.csv
│   ├── locust_exceptions.csv
│   ├── summary.json
│   ├── manifest.json
│   └── checks.json
├── 10k-burst-report.md                (rendered by SRE)
├── 10k-burst-report-template.md       (skeleton)
└── 10k-burst/
    ├── ... (same shape as 1k-rps/)
```

---

## Status

| Run | State                                                                       |
|-----|-----------------------------------------------------------------------------|
| D1  | ⏳ Owed — SRE to run on the four-node generator and publish `1k-rps-report.md`. |
| D2  | ⏳ Owed — SRE to run with the `burst_10k` shape and publish `10k-burst-report.md`. |

Once both reports land, update `agies-bussiness.md` §12 "Decision
latency" to cite the measured numbers and the v1.4.0 line-edit deletes
the "see §12 — only measured number is the dry-run" caveat introduced
by L1.
