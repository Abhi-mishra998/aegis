# Tenant Data Requests

*GDPR right-to-portability (data export) and right-to-erasure (PII redaction). The audit chain stays append-only; erasure means redaction with cryptographic markers, not deletion.*

## Two request types

| Request | Action | Tool | SLA |
|---|---|---|---|
| Right to Portability | Export all tenant data to a signed TAR | `scripts/ops/export_tenant.py` | 30 days |
| Right to Erasure | Redact PII fields in place (no deletion) | `scripts/ops/redact_tenant_pii.py` | 72 hours |

Both operations require **written sign-off** from the platform admin AND the requesting tenant's legal representative. The approval reference number is logged in the request ticket.

## Right to Portability — export

### What the export contains

```bash
.venv/bin/python scripts/ops/export_tenant.py \
  --tenant-id <TENANT_UUID> \
  --output /tmp/export/
```

The TAR archive contains:

| File | Source | Notes |
|---|---|---|
| `audit_logs.jsonl` | `acp_audit.audit_logs` | All signed audit events for the tenant |
| `usage_events.jsonl` | `acp_usage.usage_records` | All billing records |
| `agent_list.json` | `acp_registry.agents` + `permissions` | Registered agents + tool grants |
| `incidents.jsonl` | `acp_audit.acp_incidents` + comments | Open and resolved incidents |
| `notes.jsonl` | `acp_audit.audit_notes` | Analyst notes on audit rows |
| `transparency_roots.jsonl` | `acp_audit.transparency_roots` | Daily Merkle roots — let the recipient verify chain integrity |
| `manifest.json` | computed | SHA-256 of every file in the archive |

### Delivery

Deliver via secure channel:

- Signed S3 pre-signed URL with 24-hour expiry (preferred).
- Encrypted email attachment for small archives.
- Hand-delivered hardware drive for very large archives.

Retain a copy for 30 days per legal-hold policy.

### Verification

The recipient can verify the archive:

```bash
# Verify SHA-256s match the manifest
sha256sum -c manifest.json

# Verify the audit chain in the exported file
.venv/bin/acp verify-chain --input audit_logs.jsonl
```

## Right to Erasure — redaction

### Important

Audit logs are append-only by design. **Erasure means *redaction*, not deletion.** Deleting rows would break the chain, the daily root, and the platform's compliance posture.

### What gets redacted

```bash
.venv/bin/python scripts/ops/redact_tenant_pii.py --tenant-id <TENANT_UUID>
```

The script:

- Hashes `user_id` fields with a per-tenant salt.
- Replaces free-text `metadata` fields containing known PII patterns (email, phone, SSN) with `[REDACTED:<sha256>]`.
- Writes a redaction record to a separate `pii_redactions` table referencing the affected audit rows.
- Writes a chain marker row to the audit chain `action="pii_redaction"` so the redaction itself is auditable.

### What does NOT get modified

- `event_hash`, `prev_hash`, `signature`, `key_fingerprint` — chain fields stay intact.
- `tenant_id`, `agent_id`, `action`, `tool`, `decision` — operational fields, not PII.
- The daily Merkle root — already sealed; redaction does not retroactively change it.

The chain stays valid after redaction. The recipient can re-verify with `acp verify-chain`.

### Trade-off

The redaction approach trades "true deletion" for "chain integrity". The original PII value is gone from the platform — the SHA-256 stub is one-way. But the row itself stays, allowing the chain and the historical record to remain verifiable.

For tenants who demand true deletion: there is no path. The platform's compliance posture (SOC 2, EU AI Act) requires the audit chain to be tamper-evident, which requires the rows. Redaction satisfies most regulatory frameworks; "true deletion" would not.

## Approval workflow

Both operations require dual sign-off:

1. **Platform admin** approves via `POST /admin/tenant-data-requests` with `action=approve` and a reference to the legal documentation.
2. **Requesting tenant's legal rep** confirms via email or out-of-band channel; the platform admin records the confirmation reference.
3. Only after both approvals does the script run.

The approval flow is logged in:

- The audit chain (`action="tenant_data_request_approved"` with the requester and approver identities).
- The platform's incident tracker.

## Right of access (the lighter cousin)

Some regulations allow tenants to request a "summary" rather than a full export. The summary can be generated from existing UI views:

- Audit Trail → Export PDF.
- Compliance → Generate report for the requested period.

The summary is delivered in 24 hours typically and does not require the full TAR-export workflow.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Export tarball corrupt | Network interrupt mid-stream | Re-run; the script is idempotent |
| Redaction fails with FK violation | A note references an audit row being redacted | Notes carry no PII; verify only the audit row is touched, not the note |
| `acp verify-chain` fails after redaction | A field used in the canonical hash was modified | The redaction script only touches non-chain fields; if this happens, file a bug |
| Tenant requests deletion not redaction | Misunderstanding of the platform's contract | Educate via the chain integrity guarantee; refer to the [Cryptographic Audit Chain](../security/crypto-audit-chain.md) |

## Audit trail for the request itself

Every export and every redaction produces multiple audit rows:

- `action="tenant_data_request_received"` at intake.
- `action="tenant_data_request_approved"` after dual sign-off.
- `action="tenant_data_exported"` or `"tenant_pii_redacted"` after the script runs.
- `action="tenant_data_request_completed"` when the artifact is delivered.

The audit chain proves the request was handled.

## What this process does NOT do

- **Modify the daily Merkle root.** Redactions do not retroactively alter sealed roots. A redacted row's content hash changes; the daily root from the day the row was sealed does not.
- **Propagate to backup copies.** Backups taken before the redaction still contain the PII. Operators must re-run redaction against backup-restored stacks if compliance requires.
- **Customer-side data on the tool side.** A tool that called out to a third party (e.g., emailed a customer) cannot be retracted. Aegis can prove what was sent; it cannot un-send.

## SLA summary

| Request | SLA from approval | Typical |
|---|---|---|
| Portability | 30 days | 24 hours |
| Erasure | 72 hours | 4 hours |
| Access (summary only) | 30 days | 1 hour (UI export) |

## Next

- [Audit service](../services/audit.md) — the chain stays intact
- [Cryptographic Audit Chain](../security/crypto-audit-chain.md) — why redaction works
- [Compliance UI](../ui/operations/compliance.md) — the lighter access flow
- [Forensics UI](../ui/operations/forensics.md) — the analyst view of redaction events
