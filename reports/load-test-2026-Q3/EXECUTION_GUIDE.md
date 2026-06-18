# Load-test execution guide (Track D1 + D2)

**Audience:** ByteHubble SRE running the v2.0 production load tests.
**Owner:** SRE Lead.
**Version:** 1.0 · 2026-06-18.

This is the step-by-step for the SRE running the Track D1 (1k RPS sustained 30 min) and Track D2 (10 000-VU burst 5 min) tests. The harness is shipped at `tests/load/v2_realistic_user.py` + `tests/load/v2_realistic_burst.py` + `tests/load/soak.py` (--user-class / --shape). The report skeletons live at `reports/load-test-2026-Q3/1k-rps-report-template.md` and `10k-burst-report-template.md`. This guide is the bridge.

---

## 1. Pre-flight checklist

Run this before booking the maintenance window. All items must be ✅.

| # | Check                                                                                          | Command / source                                                          | ✅ |
|---|------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|----|
| 1 | Engineering has shipped the v2.0 build to prod-ha (Track H complete).                          | `git tag -l v2.0-pre-deploy`; ALB target health both healthy.              |    |
| 2 | Five test tenants exist + reserved (don't reuse paying-customer tenants).                      | `tests/load/soak.py` provisions if `--tenants 5` and they don't exist.    |    |
| 3 | `INTERNAL_SECRET` for prod-ha available to the operator (read-only access).                    | SSM Parameter Store / 1Password.                                          |    |
| 4 | 4-node load generator provisioned in `ap-south-1` (same region as the ALB).                    | Terraform `infra/terraform/environments/load-gen/` if it exists, else manual EC2 c6i.2xlarge × 4. |    |
| 5 | `locust` ≥ 2.20 installed on every generator node + Python 3.11.                               | `locust --version` on each node.                                          |    |
| 6 | Generator nodes can reach `https://ha.aegisagent.in` (ALB allow-list check).                   | `curl -fsS https://ha.aegisagent.in/status` from each node.                |    |
| 7 | `aegis-verify` installed (for the post-run chain-integrity check).                              | `pip install aegis-aevf==1.1.0` then `aegis-verify --version`.            |    |
| 8 | Grafana access (operator can pull snapshots for the report).                                   | Grafana login + role with view on `aegis-customer-slo`.                   |    |
| 9 | PagerDuty quiet-period scheduled for the run window.                                            | PagerDuty schedule → maintenance window 09:00-12:00 IST `<DATE>`.         |    |
| 10 | Customer notification optional but recommended for the 10k burst (banner on `/status`).        | If used, follow `docs/operations/incident-response.md` §3.2.              |    |

If any row is unchecked, stop. Fix or escalate.

---

## 2. D1 — sustained 1k RPS / 30 minutes

### 2.1 Run

From the *master* load-generator node:

```bash
export GATEWAY_URL=https://ha.aegisagent.in
export INTERNAL_SECRET=$ACP_PROD_INTERNAL_SECRET

python tests/load/soak.py \
    --user-class V2RealisticUser \
    --shape sustained \
    --users 1000 \
    --spawn-rate 50 \
    --duration 30m \
    --tenants 5 \
    --reports-dir reports/load-test-2026-Q3/1k-rps
```

The orchestrator handles tenant provisioning, JWT minting, manifest write, locust launch, and post-run checks. The CSVs land under `reports/load-test-2026-Q3/1k-rps/<timestamp>/`.

### 2.2 Per-node fan-out (for the 4-node cluster)

The single-node command above generates ~1k RPS from one box. To distribute across 4 nodes:

```bash
# Node 1 (master + orchestrator) — see §2.1.
# Nodes 2-4 (workers) — locust worker mode:
locust -f tests/load/v2_realistic_user.py:V2RealisticUser \
       --worker \
       --master-host <NODE_1_PRIVATE_IP>
```

The master expects `--expect-workers 3` if you want a fail-fast on missing nodes. Add `--master --expect-workers 3` to the §2.1 locust args. (Orchestrator passthrough for `--master`/`--expect-workers` not yet wired — pass via env var `EXTRA_LOCUST_ARGS` if needed, or run locust directly with the same flags as §2.1.)

### 2.3 During the run

Watch:
- `https://aegisagent.in/slo` — error rate panel (should stay < 0.5%); p95 panel (should stay < 500 ms).
- `tests/load/soak.py` stdout — locust prints aggregate every 2 s.
- Grafana `aegis-customer-slo` board — the burst-window panels.

If error rate exceeds 1% sustained for 60 s, abort:
```bash
# Ctrl+C the master. Orchestrator handles tenant teardown.
```
Investigate before retrying.

### 2.4 Post-run

The orchestrator runs `tests/load/post_run_checks.py` automatically. Verify:
- `reports/load-test-2026-Q3/1k-rps/<ts>/summary.json` shows `pass: true`.
- `aegis-verify --range yesterday today` shows no chain violation.

Render the report:
```bash
cp reports/load-test-2026-Q3/1k-rps-report-template.md \
   reports/load-test-2026-Q3/1k-rps-report.md
$EDITOR reports/load-test-2026-Q3/1k-rps-report.md
```
Fill in measured numbers from `summary.json` + locust CSV.

---

## 3. D2 — 10 000-VU burst / 5 minute hold

### 3.1 Run

Same orchestrator, different shape:

```bash
export GATEWAY_URL=https://ha.aegisagent.in
export INTERNAL_SECRET=$ACP_PROD_INTERNAL_SECRET

python tests/load/soak.py \
    --user-class V2RealisticUser \
    --shape burst_10k \
    --tenants 5 \
    --reports-dir reports/load-test-2026-Q3/10k-burst
```

The `burst_10k` shape (`tests/load/v2_realistic_burst.py:BurstShape`) overrides `--users`, `--spawn-rate`, `--duration`. Total wall-clock ≈ 7 min: 60 s ramp to 10 000 → 5 min hold → 60 s ramp down → 60 s cooldown.

### 3.2 4-node distribution

10 000 VUs from one node is too much for a single c6i.2xlarge (memory pressure, file-descriptor exhaustion). Split across 4 worker nodes by adding `--master --expect-workers 3` on the master and `--worker --master-host <ip>` on the other three. See §2.2.

### 3.3 During the burst

Watch for:
- **5xx storm** — the failure mode. p95 going above 1 500 ms is expected at peak; a sustained > 1 % 5xx is a fail.
- **Graceful 429** — the success mode. Gateway sheds load with 429; rate-limit panel shows tenants hitting their cap; this is a PASS.
- **Behavior firewall outage** — search `audit_logs` for `behavior_service_unavailable` rows during the burst window. Zero rows is the pass criterion.

If a 5xx storm fires, abort and capture the Grafana snapshot for the postmortem.

### 3.4 Recovery window

The shape's last phase ramps down to 0 over 60 s, leaving 60 s of true cooldown. Watch the p95 panel — it must return to the D1 baseline within the 90 s window after ramp-down ends.

If recovery > 90 s, document the cause in the §3 "Anomalies" section of the 10k-burst report. Likely causes: behavior-firewall warm-up, OPA bundle cache repopulation, RDS connection-pool re-establish.

### 3.5 Post-run

```bash
cp reports/load-test-2026-Q3/10k-burst-report-template.md \
   reports/load-test-2026-Q3/10k-burst-report.md
$EDITOR reports/load-test-2026-Q3/10k-burst-report.md
```

Fill in headline + recovery-window comparison + shed-load behaviour breakdown.

---

## 4. Acceptance & sign-off

### 4.1 D1 pass criteria

| Metric                  | Pass line                                |
|-------------------------|------------------------------------------|
| p50                     | < 100 ms                                  |
| p95                     | < 500 ms                                  |
| p99                     | < 1 500 ms                                |
| Error rate              | < 0.5 %                                   |
| Audit-chain post-run    | no violation                              |
| Quiet-tenant p99 fairness | within +20 % of baseline                 |

### 4.2 D2 pass criteria

| Metric                                                | Pass line                              |
|-------------------------------------------------------|-----------------------------------------|
| Burst-window p95                                       | < 1 500 ms                              |
| 5xx storm during burst                                 | none                                    |
| `behavior_service_unavailable` audit rows              | zero                                    |
| Recovery p95 back to D1 baseline                       | within 90 s of ramp-down end            |
| Audit-chain post-run                                   | no violation                            |

A failure on any row blocks publication of the corresponding report. The fix lands in the next sprint, not the report.

### 4.3 Sign-off

After both reports are committed:

1. SRE on-call signs the report's sign-off block.
2. Engineering Lead signs.
3. Engineering Lead updates `agies-bussiness.md` §12 latency line to cite the measured numbers, removing the v1.3.0 dry-run caveat introduced in L1. New biz doc tag: v1.4.0.
4. Engineering Lead updates `SPRINT.md` §13 "Evidence" rows D1 and D2 to ✅ with commit refs.

---

## 5. Tear-down

The orchestrator automatically tears down test tenants on a successful run (deletes from `acp_identity.tenants` table — audit + usage rows stay because they are append-only).

To skip tear-down for forensics:

```bash
python tests/load/soak.py ... --no-teardown
```

Tear down manually later via `scripts/ops/delete_tenant.py <tenant_id>` (still loops past the audit-log carve-out per `docs/operations/retention-policy.md` §4).

---

## 6. Troubleshooting

| Symptom                                              | Likely cause                                        | Action                                                                                              |
|------------------------------------------------------|----------------------------------------------------|------------------------------------------------------------------------------------------------------|
| `locust: command not found`                          | Generator node missing locust install.              | `pip install locust==2.20.0`.                                                                       |
| `SOAK_MANIFEST not set` on workers                   | Manifest only written on master.                    | Workers don't read SOAK_MANIFEST; they get tenant info from the master via the locust protocol.    |
| Sustained 5xx during the warm-up phase               | OPA bundle cache cold or behavior-firewall starting | Wait 60 s after the v2.0 deploy before kicking off the test.                                       |
| p95 well above SLO from first second                 | Generator → ALB RTT high (cross-region run).        | Verify generator is in `ap-south-1`. Cross-region adds 80+ ms.                                      |
| Chain-verify failure after the run                   | A test tenant's `audit_logs` row got mutated.        | Sev-0 incident. `docs/runbooks/audit_chain_violation.md`. Do NOT retry the test until resolved.    |
| Tenant teardown fails with FK constraint              | Audit / usage rows reference the tenant.            | Expected. Use `--no-teardown`; audit-log rows stay per retention policy.                            |

---

## 7. Change log

| Version | Date       | Author        | Notes                                                                                              |
|---------|------------|---------------|----------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Security Eng + SRE | First publication. Closes the operational gap between the harness (committed earlier in sprint) and the report numbers SRE owes. |
