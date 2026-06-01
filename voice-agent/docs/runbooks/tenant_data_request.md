# Tenant Data Request Runbook

## Purpose
Handle GDPR / right-to-portability and right-to-erasure requests from tenants.

## Right to Portability (data export)

```bash
# Export all tenant data to a signed TAR archive
.venv/bin/python scripts/ops/export_tenant.py --tenant-id <TENANT_UUID> --output /tmp/export/

# The archive contains:
#   - audit_logs.jsonl      (all audit events)
#   - usage_events.jsonl    (billing records)
#   - agent_list.json       (registered agents + permissions)
#   - manifest.json         (sha256 of every file)
```

Deliver the archive to the tenant via a secure channel (signed S3 pre-signed URL recommended). Retain a copy for 30 days per legal hold policy.

## Right to Erasure (PII redaction)

**Important**: Audit logs are append-only. Erasure means _redaction_, not deletion.

```bash
# Redact PII fields, write sha256-hashed redaction record + chain marker
.venv/bin/python scripts/ops/redact_tenant_pii.py --tenant-id <TENANT_UUID>

# Verify chain is still valid after redaction
.venv/bin/acp verify-chain
```

What is redacted:
- `user_id` fields hashed with a per-tenant salt
- Free-text `metadata` fields containing known PII patterns (email, phone, SSN)
- Original values replaced with `[REDACTED:<sha256>]`

What is NOT modified:
- `hash`, `prev_hash`, `root_hash` chain fields — these are never touched
- `action`, `tool_name`, `decision` fields — these are operational, not PII

## SLA
- Portability requests: 30 days
- Erasure requests: 72 hours from written request

## Approval
Both operations require written sign-off from the ACP admin and the requesting tenant's legal representative. Log the approval reference number in the request ticket.

## See also
- `scripts/ops/export_tenant.py`
- `scripts/ops/redact_tenant_pii.py`
