# Disaster Recovery Runbook (Sprint 9)

> Procurement-grade DR posture for Aegis: documented RTO/RPO per data
> store, an automated weekly restore drill, and an evidence artifact
> uploadable to S3 for the CISO who asks "show me your last successful
> restore".
>
> Audit context (`AUDIT_REPORT.md` §S7): the prior runbook claimed
> backups but proved nothing. Sprint 9 closes that gap with
> `scripts/ops/dr_evidence.py` — a single JSON artifact per drill,
> signed by the same ed25519 path the receipt chain uses.

---

## 1 · Targets — RTO + RPO per data store

| Data store    | RTO (recovery time) | RPO (data loss)      | Mechanism |
|---------------|---------------------|----------------------|-----------|
| **RDS Postgres** | 30 minutes        | 5 minutes            | Multi-AZ failover (automatic, <1 min) **OR** restore from `acp_backups` S3 bucket (~25 min). Point-in-time recovery covers the 5-minute RPO. |
| **ElastiCache Redis** | 5 minutes    | 0 (ephemeral by design) | Replication group with automatic failover. Any data here (rate-limit counters, kill-switch flags, SSE pubsub) is reconstructable from RDS within seconds — see §4. |
| **S3 (audit chain, backups, ALB logs)** | 1 hour | 0 (versioned + replicated)  | Bucket versioning + cross-region replication to a hardened backup account. |
| **KMS / Secrets Manager** | N/A — control plane | N/A — control plane | AWS-managed availability; for an account-level compromise see §6. |

Procurement notes:

- RTO is **end-to-end** — when the runbook is followed start-to-finish,
  the public ALB serves 200 on `/health` within the stated window.
- RPO is measured from the **last committed write** the application
  acknowledged.
- These numbers are pinned by the **weekly restore drill** (§3) — the
  evidence artifact records the actual observed RTO each week so a
  regression surfaces the next Sunday morning, not on the day of an
  incident.

---

## 2 · Backup inventory — what's in S3

The `acp-backups-prodha-${ACCOUNT_ID}` bucket holds:

```
backups/postgres/<YYYY-MM-DD>/acp-postgres-prodha-<ts>.dump.age   # age-encrypted pg_dump
backups/postgres/<YYYY-MM-DD>/MANIFEST.json                       # sha256 + signer + size
backups/redis/<YYYY-MM-DD>/redis-<ts>.rdb.age                     # ElastiCache snapshot export
restore_drills/<YYYY-MM-DD>/evidence.json                         # see §3
releases/current.tar.gz                                           # current application bundle
releases/<git-sha>.tar.gz                                         # immutable per-commit archive
```

Retention: 730 days on `backups/`, 90 days on `restore_drills/`,
forever on `releases/` (governed by the bucket lifecycle in
`infra/terraform/modules/s3`).

---

## 3 · The weekly restore drill

### What runs

`scripts/ops/restore_drill.sh` (pre-Sprint-9) spins an isolated
docker-compose project, downloads the most recent encrypted dump,
decrypts via age, restores into a throwaway Postgres, runs
`scripts/ops/reconcile.py` + `/audit/logs/verify` and exits non-zero on
any failure.

Sprint 9 adds `scripts/ops/dr_evidence.py` — a thin wrapper that:

1. Invokes `restore_drill.sh`.
2. Captures: timestamp, dump file sha256, restore-side row counts,
   reconcile result, the audit-chain verification verdict, the EC2
   instance id + IAM role that ran the drill, the drill duration.
3. Writes the JSON to `reports/restore_drill/<UTC-iso>.json`.
4. Uploads to `s3://acp-backups-prodha-${ACCOUNT_ID}/restore_drills/<date>/evidence.json`.
5. Signs the JSON with the same ed25519 receipt-signing key
   (`RECEIPT_SIGNING_*` env) so a buyer can re-verify the artifact
   offline using the published Aegis signing public key.

### When it runs

GitHub Actions: `.github/workflows/weekly-restore-drill.yml` — Sunday
04:00 UTC, posts the evidence JSON URL to the on-call Slack channel.

### Quarterly chaos drill

The first Tuesday of each quarter the SRE on-call runs the
**unscheduled** version — `dr_evidence.py --chaos` — which additionally
simulates the "AZ A is gone" failure mode by killing the primary RDS
instance via `aws rds reboot-db-instance --force-failover`. The drill
times the cut-over and reports whether the application served
`200 /health` within RTO.

---

## 4 · The redis RPO=0 contract

Redis carries:

- rate-limit counters (token bucket)
- kill-switch flags
- SSE pubsub channels
- in-process JWT validation cache

None of these is the source of truth for any decision. On a total
Redis loss:

- Rate-limit counters reset → first request after restart MAY succeed
  even if the bucket was empty. Acceptable degradation per the
  Sprint 1.5 "fail closed with a 60s ratchet" guarantee.
- Kill switches reload from `acp_identity.tenants.kill_switch_active`
  within 30 seconds (`services/decision/main.py:L109-119`).
- SSE channels drop — clients reconnect transparently.
- JWT cache rebuilds on demand.

The replication-group's multi-AZ failover masks this entirely under
single-AZ failure. The RPO=0 claim refers to the application's
**ability to operate** — there is no business state in Redis to lose.

---

## 5 · Restore — operator playbook

When a restore is required (not a drill):

```bash
# 1. Get the on-call to confirm an incident ticket exists.
export INCIDENT_ID=INC-2026-XXX

# 2. Source the SSM-backed credentials.
eval "$(scripts/ops/load_prod_creds.sh)"

# 3. Pick the recovery point — list available dumps + their sha256s.
scripts/ops/list_recovery_points.sh

# 4. Run the restore (writes evidence under reports/restore/).
scripts/ops/restore_drill.sh \
    --target=prod-ha \
    --recovery-point="<dump-key-from-step-3>" \
    --incident-id="${INCIDENT_ID}"

# 5. Verify the application's data plane is back.
curl -sf https://ha.aegisagent.in/health
curl -sf https://ha.aegisagent.in/system/health | jq '.services'

# 6. Promote the restored instance to primary (Multi-AZ failover does
#    this automatically; manual step only for the cross-region case).
aws rds promote-read-replica --db-instance-identifier=<replica-id>

# 7. Attach the evidence JSON to the incident ticket.
```

The incident ticket MUST link to the evidence artifact (S3 URI from
step 4 stdout) before close. The audit team reviews evidence artifacts
quarterly.

---

## 6 · Account-level compromise — break-glass

If AWS root account credentials are compromised:

1. The AWS Account Activation policy (`infra/terraform/modules/iam`)
   limits root-account usage to MFA-required console sessions only.
2. The break-glass procedure is documented separately at
   `docs/runbooks/account_compromise.md` — Sprint 9 follow-up.
3. The Aegis cryptographic audit chain (ed25519 + Merkle) means even
   a malicious account holder cannot retroactively rewrite history
   — the signed roots are external attestation a buyer can verify.

---

## 7 · Evidence artifact — what's in it

```json
{
  "drill_id":             "dr-2026-07-14T04-00-00Z",
  "started_at":           "2026-07-14T04:00:00Z",
  "finished_at":          "2026-07-14T04:21:32Z",
  "duration_seconds":     1292,
  "target_environment":   "prod-ha",
  "recovery_point": {
    "dump_s3_uri":        "s3://acp-backups-prodha-.../postgres/2026-07-13/acp-postgres-prodha-040013.dump.age",
    "dump_sha256":        "f7c1a3...",
    "dump_size_bytes":    734598123
  },
  "restored_row_counts": {
    "audit_logs":         18234123,
    "tenants":            14,
    "agents":             318
  },
  "chain_verification": {
    "verifier":           "acp verify-chain",
    "rows_verified":      18234123,
    "verdict":            "intact",
    "breaks":             []
  },
  "reconcile_result": {
    "audit_without_usage": 0,
    "usage_without_audit": 0,
    "outbox_oldest_age":   0
  },
  "drill_runner": {
    "instance_id":        "i-0deadbeef...",
    "iam_role":           "arn:aws:iam::628478946931:role/acp-prodha-drill-runner"
  },
  "signature": {
    "algorithm":          "ed25519",
    "kid":                "aegis-receipt-2026-q2",
    "value":              "MEUCIQCx..."
  }
}
```

The signature is over canonical JSON of every field except `signature`.
Any buyer can re-verify with the Aegis signing public key (published
at `https://aegisagent.in/.well-known/signing-keys.json`).
