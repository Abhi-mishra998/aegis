# Disaster Recovery

**Audience:** Customer security teams, ByteHubble SRE, auditors.
**Owner:** ByteHubble SRE.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/runbooks/dr.md` — engineering-internal step-by-step failover script. This file is the customer-facing audit posture; the engineering runbook is the operational counterpart.
- `docs/runbooks/restore_drill.md` — backup-restore drill procedure.
- `docs/operations/incident-response.md` §5 — DR triggers as incident classes.
- `docs/operations/retention-policy.md` §2 — backup retention windows.

This file documents the customer-facing DR posture for Aegis: target RTO and RPO, the backup architecture that underpins them, the failover procedure, the drill log capturing observed recovery time and data loss, and the cadence + owner for ongoing drills.

---

## 1. Targets

| Metric                                    | Customer-facing target | Engineering capability (tighter) | Source of truth                 |
|-------------------------------------------|------------------------|----------------------------------|---------------------------------|
| **Recovery Time Objective (RTO)** for full service | **4 hours**            | ≤ 30 minutes for the Multi-AZ failover path; ≤ 4 hours for the cross-region S3-restore path. | `docs/runbooks/dr.md` §1 RTO per data store. |
| **Recovery Point Objective (RPO)** for any data store | **15 minutes**         | ≤ 5 minutes via RDS automated snapshots + WAL replication. | `docs/runbooks/dr.md` §1 RPO per data store. |
| **Maximum tolerable downtime** for the public Merkle bucket | 24 hours      | n/a — cross-region replicated.   | `infra/terraform/environments/prod-ha/main.tf` (bucket replication block). |

ByteHubble commits the *customer-facing* targets in the underlying Subscription Agreement and DPA; the engineering capability is the internal target the SRE on-call works to, leaving operational headroom against the external commitment.

---

## 2. Backup architecture

### 2.1 Postgres RDS

- **Multi-AZ primary** in `<PRIMARY_AWS_REGION>` (`infra/terraform/environments/prod-ha/main.tf:184` `multi_az=true`).
- **Automated snapshots** retained per `docs/operations/retention-policy.md` §2 — 35 days nightly, 12 months monthly.
- **Cross-region read replica** in `<DR_AWS_REGION>` lagging by < 60 seconds.
- **Point-in-time recovery** (PITR) covers the RPO window.

### 2.2 ElastiCache Redis

- **Replication group** with automatic failover.
- Data here is ephemeral by design: rate-limit counters, kill-switch flags, JWT cache, SSE pubsub state. All are reconstructable from RDS within seconds of recovery — no separate backup taken.

### 2.3 S3 — public Merkle bucket

- **Bucket:** `s3://aegis-public-roots-628478946931` (versioned).
- **Cross-region replication** to `<DR_AWS_REGION>` bucket.
- Loss of the primary bucket does not destroy data — all roots remain in the replica, the RDS `transparency_roots` table, and any external archive a customer has taken.

### 2.4 Application code

- Container images pinned by SHA in `infra/docker-compose.yml`; no `:latest` anywhere.
- Deploy artefact uploaded to S3 with sha-256 metadata before SSM rollout — recoverable from prior versions in case of bad deploy.

---

## 3. Failover procedure

The operational script lives at `docs/runbooks/dr.md`. The customer-facing summary follows.

### 3.1 Detect

- Alertmanager fires on multi-AZ failure or a primary-region health-check failure.
- SRE on-call acknowledges per `docs/operations/incident-response.md` §1 (Sev-0 or Sev-1 depending on scope).

### 3.2 Decide

- Loss of one AZ → automatic Multi-AZ failover within RDS (no human action required). Expected time-to-recovery: < 1 minute.
- Loss of the primary region → human decision to promote the cross-region replica and re-point the ALB DNS. SRE incident commander makes the call after consulting status of `<PRIMARY_AWS_REGION>` with AWS.

### 3.3 Act

For region failover:

1. Promote the cross-region RDS replica to primary in `<DR_AWS_REGION>`.
2. Re-point Aegis application ALBs to the DR region (Terraform-managed `dr_active` flag).
3. Re-issue Clerk JWKS cache invalidation so cached keys re-fetch against the new region's gateway.
4. Verify `/status` reports 12 components operational.
5. Run the audit-chain post-recovery verification (`aegis-verify --range yesterday today`).
6. Open the post-recovery validation matrix per `docs/runbooks/dr.md` §4.

### 3.4 Communicate

Per `docs/operations/incident-response.md` §3.2 — Sev-0 customer notification within 72 hours, status page updated on change.

### 3.5 Stand down

Once primary region is healthy and traffic has been re-balanced, the cross-region replica is rebuilt from the new primary. The incident closes once `/status` is operational in the canonical region and the post-mortem under §6 below is scheduled.

---

## 4. Drill log

Quarterly DR drills exercise §3 end-to-end against the live infrastructure. The drill is staged off-peak in a customer-notified maintenance window.

Each drill row captures: date, scope (Multi-AZ failover / region failover / S3 restore), observed RTO, observed RPO, observed deviations from the runbook, and the operator who ran it.

| Drill date | Scope                              | Observed RTO | Observed RPO | Deviations from runbook | Operator |
|------------|------------------------------------|--------------|--------------|-------------------------|----------|
| _SRE to populate after Track E1 executes — see SPRINT.md §8 E1._ | | | | | |

> **Status — 2026-06-18.** Track E1 (`SPRINT.md` §8) is scheduled inside the v2.0 sprint. The first row above is owed by the SRE team at drill completion. Acceptance for SPRINT.md §13 (Definition of Done) requires the observed RTO < 4 h and RPO < 15 min to be recorded here.

---

## 5. Cadence and ownership

| Cadence     | Drill                              | Owner                                  |
|-------------|------------------------------------|----------------------------------------|
| Quarterly   | Multi-AZ failover                  | SRE on-call (rotating)                  |
| Quarterly   | Backup restore (`docs/runbooks/restore_drill.md`) | SRE on-call (rotating)         |
| Annually    | Region failover (full DR drill)    | SRE Lead                                |
| Annually    | S3-restore tabletop                | Security Engineering                    |

The named owners are responsible for staging the drill, running it in the maintenance window, recording the row in §4, and writing a brief drill report under `docs/operations/drills/YYYY-MM-DD-<name>.md`. Any deviation from the runbook becomes an action item in the next sprint.

---

## 6. Post-incident postmortem

Any drill that breaches the customer-facing targets — RTO > 4 h or RPO > 15 min — triggers a Sev-1 incident report per `docs/operations/incident-response.md` §6 and a 14-day postmortem. The postmortem also goes to the customer if a real incident motivated the drill.

---

## 7. Customer questions we expect

The following questions have been asked during procurement and are addressed here so the SRE on-call does not have to re-derive the answer each time.

| Question                                                                                                                       | Answer / pointer                                                                                                  |
|--------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| "How do you measure RTO? From the first symptom or from the first ack?"                                                        | From the first symptom (PagerDuty event time) to `/status` returning 12-component operational. Logged in §4.      |
| "If the primary AWS region is down, what is the practical RTO?"                                                                 | ≤ 4 hours customer-facing. Engineering target ≤ 30 minutes via cross-region replica promotion. See §3.3.          |
| "What is the RPO for the audit log specifically?"                                                                              | ≤ 15 minutes customer-facing; ≤ 5 minutes operationally because the WAL replicates to the cross-region replica.    |
| "How do you protect against the Merkle root signing key being lost in the disaster?"                                            | The signing key is loaded via `provider_from_env()` precedence at `services/audit/signer.py:230-260`; the key is held in AWS Secrets Manager (KMS-backed) with the same cross-region replication as RDS. Recovery procedure in `docs/runbooks/key_rotation.md`. |
| "Do you publish the drill log?"                                                                                                 | The most recent drill row is published in §4. Customer can request the underlying drill report under NDA.        |

---

## 8. Change log

| Version | Date       | Author | Notes                                                                                                                                                                                                                  |
|---------|------------|--------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | SRE    | First publication. Customer-facing target RTO 4 h / RPO 15 min established. Drill log left blank for SRE to populate at Track E1 close per SPRINT.md §8. Closes audit finding C7 (engineering side; observed numbers owed). |
