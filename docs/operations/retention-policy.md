# Data Retention Policy

**Audience:** Customer privacy counsel, ByteHubble Engineering + Legal, auditors.
**Owner:** ByteHubble Legal + Security Engineering.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/security/dpa-template.md` §7 (DPA retention summary; this file is the authoritative source).
- `docs/security/baa-template.md` §11 (HIPAA-specific retention summary; this file is the authoritative source).
- `docs/operations/incident-response.md` §6 (incident-evidence retention windows).
- `docs/runbooks/tenant_data_request.md` (operational procedure for tenant erasure / portability requests).

This policy sets the retention window for every class of data Aegis stores on behalf of customers, the operational mechanism that enforces each window, and the standing exceptions (audit-log carve-out, regulatory hold).

---

## 1. Principles

1. **Retention is bounded.** No data class is retained indefinitely. Every class has a numeric window in §2.
2. **Retention is enforced operationally, not aspirationally.** Each window names the cron / migration / runbook that actually evicts the data. Where the eviction is not yet automated, the gap is recorded in §6 with an owner.
3. **Retention is minimised wherever the chain of evidence allows.** The audit log is the one exception — see §4.
4. **Customer-directed deletion overrides retention.** A tenant-erasure request follows the procedure in `docs/runbooks/tenant_data_request.md`, subject only to the audit-log carve-out and any legal hold.

---

## 2. Retention windows

| Class                                | Window                                                           | Storage location                                  | Enforcement mechanism                                                                                                                            |
|--------------------------------------|------------------------------------------------------------------|---------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| Audit logs (`audit_logs`)            | **10 years** from row insert.                                    | Postgres RDS Multi-AZ.                            | No automated deletion before window; eviction at the 10-year mark uses a controlled `DROP TRIGGER` window with chain-marker insert.              |
| Operational logs (request / response, behavior firewall traces, OPA decision traces) | **90 days** from row insert.                       | Postgres RDS + structured logs in CloudWatch.    | Nightly Postgres delete via the retention cron; CloudWatch log group retention set to 90 days at terraform apply time.                          |
| Personal Data in usage records       | **24 months** from row insert; then anonymised in place.         | Postgres RDS.                                     | Monthly anonymisation job replaces identifier fields with sha-256 hashes; row remains for billing and trend analysis.                            |
| Approval card metadata               | **24 months** from creation; anonymised on the same schedule.    | Postgres RDS.                                     | Approval card carries the same anonymisation job as usage records.                                                                              |
| Database backups — nightly snapshots | **35 days** rolling.                                             | RDS automated-snapshot bucket.                    | RDS lifecycle policy.                                                                                                                            |
| Database backups — monthly snapshots | **12 months** rolling.                                           | Cross-region S3 mirror.                           | S3 lifecycle rule.                                                                                                                               |
| Public Merkle roots                  | **No deletion** — the chain is the proof.                        | `s3://aegis-public-roots-628478946931`.           | Versioned bucket; cross-region replication.                                                                                                      |
| SSE event-stream payloads            | **Ephemeral** — not persisted beyond Redis stream TTL.           | ElastiCache Redis.                                | Per-tenant Redis stream TTL of 24 hours.                                                                                                         |
| Incident timelines / postmortems     | **7 years** from incident close.                                 | Git-versioned under `docs/postmortems/`.          | Branch-protected; redacted publication per `docs/operations/incident-response.md` §6.1.                                                          |
| Tenant offboarding purge             | **30 days** from contract termination.                            | All stores above.                                  | Erasure runbook (`docs/runbooks/tenant_data_request.md`); subject to audit-log carve-out (§4).                                                  |

---

## 3. Onboarding and contract-term retention

For an active tenant, retention windows in §2 run from the date of row insert, independent of contract anniversary. Termination of the underlying Subscription Agreement triggers the offboarding sequence in §5.

---

## 4. Audit-log carve-out

The `audit_logs` table is the cryptographic source of truth that backs:

- DPA §5 (cryptographic transparency)
- BAA §11 (HIPAA § 164.312 (b) audit-controls obligation)
- SOC2 CC-7 (security operations evidence)

Deleting an `audit_logs` row mid-window would invalidate every Merkle daily root from that day onward. Therefore:

1. **The default 10-year window is non-negotiable per row** — neither tenant-initiated erasure nor a Sub-processor change triggers row deletion.
2. **Personal Data appearing in an audit row is redacted in place.** A separate `audit_redactions` record stores the sha-256 hash of the redacted field, preserving Merkle-chain integrity. The row's `tenant_id`, `request_id`, and timestamp remain intact.
3. **Eviction at the 10-year mark** is performed in a controlled transaction that (a) drops `deny_audit_log_mutation` for the eviction transaction only, (b) deletes the rows past the 10-year mark, (c) re-creates the trigger, (d) inserts a chain-marker row noting the eviction range and the operator. The eviction is itself an audited event, captured in the next daily Merkle root.

This carve-out is disclosed to customers at contract time and is reproduced verbatim in DPA §7.3 and BAA §11.2.

---

## 5. Tenant offboarding — 30-day purge

On termination of the underlying Subscription Agreement:

| Day | Action                                                                                                                                       |
|-----|----------------------------------------------------------------------------------------------------------------------------------------------|
| 0   | Contract termination effective. Tenant scope disabled at the gateway (subsequent authenticated calls return 410 Gone).                       |
| 0–7 | Customer downloads any data they wish to retain via the portability API (`scripts/ops/export_tenant.py`).                                    |
| 7   | Final usage / billing reconciliation locks the tenant's billing windows.                                                                     |
| 8–30| Operator runs `docs/runbooks/tenant_data_request.md` "erasure" path: deletes operational logs, anonymises usage rows, and rotates any per-tenant Redis keys. The audit-log rows remain per §4. |
| 30  | Certificate of deletion is issued to the customer's designated contact, listing the categories purged, the date of purge, and the operator name. |

A customer who insists on a shorter window may request an expedited purge; ByteHubble accommodates within five (5) business days where operationally feasible.

---

## 6. Open items

Retention controls that are not yet fully automated, with owner and target. These are tracked alongside the threat-model open items.

| ID   | Open item                                                                                                                                                 | Owner   | Target sprint    |
|------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|---------|------------------|
| RT-1 | Automate the monthly anonymisation cron for usage records (currently runbook-triggered).                                                                 | SRE     | Roadmap 2026-Q4  |
| RT-2 | Add nightly Postgres delete for operational logs > 90 days (CloudWatch retention is already automated; Postgres rows still need the cron).               | SRE     | Roadmap 2026-Q4  |
| RT-3 | Publish the 10-year-mark eviction runbook (long-tail; the first eviction is not due until 2036). Drafted skeleton in `docs/runbooks/audit_eviction.md`.   | Security | 2026-Q3 draft   |
| RT-4 | Wire the public Merkle bucket versioning expiry to align with the audit eviction so that historical roots are not retained beyond the rows they witness. | Security | Roadmap 2026-Q4  |

---

## 7. Legal hold

When ByteHubble receives notice of a litigation hold, regulatory investigation, or law-enforcement preservation request:

1. The retention windows in §2 are suspended for the affected rows and storage locations.
2. The eviction crons named in §6 skip the affected rows automatically (the hold flag is set on the row at notice-receipt time).
3. The hold remains in effect until the issuing authority releases it in writing.

Legal hold is invoked by the General Counsel and recorded in the legal-hold log (private). The act of placing or releasing a hold is audited.

---

## 8. Customer-driven exceptions

Where a customer contract incorporates a stricter retention window than this policy, the contract prevails. The deviation is tracked in `docs/operations/customer_retention_overrides.md` and reviewed at every quarterly retention audit.

---

## 9. Audit and review

- This policy is reviewed annually by Legal and Security Engineering.
- A retention audit is performed quarterly: the operator runs `scripts/ops/audit_retention.py` (planned, see RT-1 / RT-2) and reconciles actual on-disk row ages against the windows in §2. Drift is investigated and the gap is closed before the next quarterly audit.
- The audit output is filed at `reports/retention-audit-YYYY-Qn.md` and is included in the SOC2 evidence package.

---

## 10. Change log

| Version | Date       | Author              | Notes                                                                                                                                                |
|---------|------------|---------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Legal + Security Eng | First publication aligned to GDPR, CCPA, and HIPAA windows. Authoritative source for retention windows referenced from DPA §7.2 and BAA §11.1. Closes audit finding C6. |
