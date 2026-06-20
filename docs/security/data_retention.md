# Aegis data retention policy

Every data class has a retention floor (minimum) and a retention ceiling (maximum). Customers can configure within those bounds; Aegis cannot override.

## Retention by data type

| Data | Floor | Default | Ceiling | Why |
|------|-------|---------|---------|-----|
| Audit log rows (`audit_logs`) | **180 days** | 365 days | 7 years | EU AI Act Art. 12 (180 d minimum), India DPDP Sec. 8(5) (365 d), SOX § 802 (7 y for finance customers) |
| Incident records (`incidents`) | 180 days | 365 days | 7 years | Tied to the audit row that triggered them |
| Decision events (cached) | 0 (ephemeral) | 7 days | 30 days | Recovered from audit row if needed |
| Flight-recorder timelines | 7 days | 30 days | 90 days | Performance forensics; audit row is the durable record |
| Policy decisions cache (Redis) | 0 | TTL 5 min | TTL 1 hour | Recomputed deterministically; cache miss is correctness-preserving |
| Notifications | 30 days | 90 days | 365 days | Operator inbox |
| Backups (S3 Object Lock GOVERNANCE) | 30 days | 30 days | 90 days | DR window only |
| CloudTrail logs (S3 Object Lock COMPLIANCE) | 180 days | 180 days | 7 years | Tamper-evident even against root |
| Cryptographic Merkle roots (public S3 + Object Lock COMPLIANCE) | **forever** | forever | forever | The chain must remain customer-verifiable |
| Workspace / Tenant rows | until close + 30 days | until close + 30 days | until close + 30 days | Allows recovery during the 30-day undo window |
| User PII (email, role) | until off-boarded | until off-boarded | until off-boarded + 30 days | GDPR / DPDP minimum-necessary |
| Demo tenants (`is_demo = true`) | 0 | 30 minutes | 30 minutes | Hard-deleted by the hourly reaper (Sprint EH-2) |

## How to set tenant retention

Owner or Admin updates via the API:

```bash
PATCH /workspace/system-values
content-type: application/json
authorization: Bearer <owner-jwt>

{ "audit_retention_days": 730 }     # 2 years
```

The platform validates: must be ≥ data-type floor; ≤ data-type ceiling. Out-of-range values return 400.

## How to export before retention

Two routes, both customer-initiated:

```bash
# 1. Filtered audit-row export (CSV + JSON)
POST /audit/logs/export
?period_start=2026-01-01&period_end=2026-06-30

# 2. Full compliance evidence ZIP (per-control CSV + Merkle proofs + verify.sh)
POST /compliance/export
?frameworks=soc2,eu_ai_act,nist_ai_rmf
```

Both return an S3 presigned URL with a 24-hour expiry. The ZIP is the standalone artefact a customer can hand to their auditor; it includes the AEVF offline verifier (`aegis-verify`) so the auditor doesn't need to trust Aegis to confirm the data is genuine.

## How deletion works

When retention is reached:

1. The hourly reaper (one per service) selects rows where `created_at < now() - retention`.
2. For audit-log rows: hard delete from the **partition** of `audit_logs` corresponding to the retention edge. Postgres partition drop is O(1).
3. The corresponding Merkle root for the deleted day's leaves stays — the public transparency chain remains intact, just the leaf bodies are gone. An auditor can still verify "as of date X, leaf count was Y and root was Z," but cannot read individual leaves.
4. The deletion itself is audit-logged with `action='retention_expiry'` so customers can prove their own compliance with retention policies.

## "Right to be forgotten" (GDPR Art. 17 / DPDP Sec. 14)

If a customer's end-user invokes right-to-erasure:

1. Operator runs `scripts/ops/redact_tenant_pii.py --user-email=<email> --tenant-id=<tid>`.
2. The script writes a sha256-hashed redaction record to `audit_logs` (action=`pii_redacted`) — the audit row is preserved (it must be, for the chain) but the PII fields are replaced with `<redacted>` in a separate `audit_logs_pii_redacted` projection table that downstream consumers read.
3. The audit chain remains valid because the original row's `event_hash` is computed over the redacted form (the operator confirms hash continuity post-redaction).

Procedure detail in `docs/runbooks/tenant_data_request.md`.

## What's NOT subject to retention

- The cryptographic transparency log roots at `s3://aegis-public-roots-…`. They are public, signed, integrity-only, and contain no customer data. They stay forever so any customer who archived an old root can verify against any later day.
- The customer signing public keys at `s3://aegis-public-roots-.../keys/`. Same reason.

## Customer-side enforcement

Customers running on prem or in their own VPC have additional obligations: their own backup retention, their own SIEM ingest retention, their own LLM provider's logging retention. Aegis tooling does NOT reach into customer-controlled systems.
