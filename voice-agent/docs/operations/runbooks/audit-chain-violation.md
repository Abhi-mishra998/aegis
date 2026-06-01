# Runbook: Audit Chain Violation

## Alert

`ChainViolationImmediate` — fires when `acp_audit_chain_violations_total > 0`. Alert evaluation interval `for: 0m` (page immediately).

## Severity

**P0.** A chain violation means the append-only audit log has been tampered with, OR a code bug broke hash chaining. Stop all writes immediately and investigate.

## Triage in 5 minutes

### 1. Confirm the violation

```bash
SSH to either EC2 host; from there:

# Verify via API
curl -sS http://localhost:8000/audit/logs/verify \
  -H "Authorization: Bearer $(.venv/bin/acp auth-token)" \
  -H "X-Tenant-ID: $TENANT" | jq '{ valid, violations, rows_checked }'
```

Healthy: `{ "valid": true, "violations": [], "rows_checked": N }`.
Unhealthy: `violations` is non-empty.

Also run the offline CLI:

```bash
.venv/bin/acp verify-chain 2>&1 | grep -E "violation|INVALID|FAIL"
```

### 2. Identify the first broken link

```bash
psql $DATABASE_URL -c "
  SELECT id, request_id, created_at, prev_hash, event_hash, chain_shard
  FROM audit_logs
  WHERE tenant_id = '<TENANT>'
  ORDER BY chain_shard, created_at, id
  LIMIT 100;
" | head -30
```

Manually walk the chain from the top. The first row whose `prev_hash` does not match the previous row's `event_hash` is the break point.

### 3. Scope the blast radius

```bash
# How many rows are after the break (potentially affected by the same incident)
psql $DATABASE_URL -c "
  SELECT count(*) FROM audit_logs
  WHERE tenant_id = '<TENANT>'
  AND created_at > '<break_timestamp>';
"
```

If the break is recent (last hour), the blast radius is small. If it's older, more rows are affected.

## Containment in 10 minutes

### 4. Stop new writes

```bash
# Pause the audit outbox worker
docker stop acp_audit

# This stops new rows entering the chain. The gateway continues writing to
# the Redis stream; the outbox drains when audit is restarted.
```

**Do NOT delete rows.** Deletion destroys forensic evidence and is irreversible.

**Do NOT roll back the database.** The current state, even with the violation, is the record of what happened. Investigators need it.

### 5. Notify

- Page the on-call security lead.
- Post the alert to the incident channel.
- Open an incident ticket with the violation details.

## Root causes, in frequency order

1. **Bug in hash computation.** A recent deploy changed `sdk/common/audit_hash.py` or `services/audit/writer.py`. Check the last 24 hours of deploys.
2. **Clock skew.** `prev_hash` from a different row due to row reordering during concurrent inserts. The chain lock should prevent this; verify `acp:audit_chain_lock:{tenant_id}` was honored.
3. **Direct database write.** A non-application connection wrote to `audit_logs`. Check `pg_stat_activity` for non-application connections. Common culprits: a debugging psql session, a misconfigured backup tool.
4. **Storage bit flip.** Extremely rare; Postgres checksums should catch it. Run `pg_dump --verify` on the affected table.
5. **Malicious tampering.** Unlikely but the most severe case. Inspect the audit row's content vs. the S3 receipt for the same audit_id — if they differ, the Postgres row was modified after upload.

## Recovery

### Code bug

If the violation is a code bug, not malicious:

1. Identify the regression (recent commit).
2. Revert the deploy to the previous known-good bundle.
3. Re-compute hashes from the last known-good root forward:

   ```bash
   .venv/bin/python scripts/maintenance/repair_audit_chain.py \
       --tenant-id <TENANT> \
       --from-row <LAST_GOOD_ID>
   ```

4. Re-run `acp verify-chain` to confirm.
5. File a regression test covering the specific failure mode.

### Direct DB write

If a non-application connection wrote rows:

1. Identify the connection via `pg_stat_activity` archive.
2. The rows written are evidence; do not delete them.
3. Add a chain marker row: `action="chain_repaired"` describing the incident.
4. Recompute the chain from the marker forward.
5. The rows written by the unauthorized connection are now part of the chain but are flagged in `metadata_json.chain_repair_reference`.

### Malicious tampering

If the audit row content disagrees with the S3 receipt:

1. The S3 receipt is the durable record.
2. Restore the affected row from the receipt.
3. Run chain repair.
4. Open a security incident; investigate the access path.

## Update transparency roots

After any chain repair, the daily Merkle roots referencing the affected day must be re-sealed:

```bash
.venv/bin/python scripts/maintenance/reseal_transparency_root.py \
    --tenant-id <TENANT> \
    --date <YYYY-MM-DD>
```

The re-sealed root carries a `prev_root_hash` link to the original (broken) root. Customers archiving daily roots can detect the repair.

## Restart the audit writer

```bash
docker start acp_audit

# Confirm the outbox drains
curl -sS http://localhost:8000/audit/outbox-depth | jq '.data.oldest_age_seconds'
# Should converge to 0 within a few minutes
```

## Post-incident

1. File an incident report including:
   - The first broken row's `id` and `request_id`.
   - The blast radius (number of affected rows).
   - The root cause (code bug / DB write / etc).
   - The recovery actions taken.
2. Add a regression test under `tests/audit/test_chain_integrity.py` covering the failure mode.
3. Re-run the soak test to confirm the fix.
4. Update this runbook if the failure mode revealed a gap.

## Customer notification

If the chain violation was visible to customers (e.g., a customer's compliance auditor would have noticed):

1. Notify affected tenants within 24 hours.
2. Explain the incident, the root cause, and the recovery.
3. Provide the re-sealed transparency root so the customer can re-verify their archive.

## Why this runbook is short

The runbook is intentionally focused on actions. The "why" is documented elsewhere:

- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) for the chain math.
- [Audit service](../../services/audit.md) for the writer implementation.

A P0 incident is not the time to read theory.

## Related code

- `services/audit/integrity.py::verify_audit_chain`
- `sdk/common/audit_hash.py::compute_event_hash`
- `services/audit/writer.py::write_signed_row`
- `services/audit/outbox_worker.py`
- `services/audit/transparency.py::reseal_daily_root`

## Next

- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) — the underlying math
- [Audit service](../../services/audit.md) — the implementation
- [Key Rotation](../key-rotation.md) — related to chain integrity
