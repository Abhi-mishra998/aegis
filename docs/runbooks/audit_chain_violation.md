# Audit Chain Violation Runbook

## Alert
`ChainViolationImmediate` — fires when `acp_audit_chain_violations_total > 0`.

## Severity
**P0** — a chain violation means the append-only audit log has been tampered with or a bug broke hash chaining. Stop all writes immediately.

## Triage

### 1. Identify the violation
```bash
# Find the first broken link
.venv/bin/acp verify-chain 2>&1 | grep "violation\|INVALID"

# Or via API
curl http://localhost:8003/audit/chain/verify | jq '.data'
```

### 2. Scope the blast radius
```bash
# Which rows are affected?
psql $DATABASE_URL -c "
  SELECT id, request_id, created_at, prev_hash, hash
  FROM audit_logs
  WHERE hash != expected_hash_column
  ORDER BY created_at
  LIMIT 20;
"
```

### 3. Containment
- **Do not delete rows** — deletion destroys forensic evidence.
- Pause the audit writer: `docker stop acp_audit` until root cause is found.
- Page the on-call security lead.

### 4. Root causes (in frequency order)
1. **Bug in hash computation** — check if a recent deploy changed `audit_chain.py`
2. **Clock skew** — `prev_hash` from a different row due to reordering on insert
3. **Direct DB write** — check `pg_stat_activity` for non-application connections
4. **Storage bit flip** — rare; check Postgres checksums

### 5. Recovery
If the violation is a code bug (not malicious), roll back the deploy and re-compute hashes from the last known-good root:
```bash
.venv/bin/python scripts/maintenance/backfill_flight_timelines.py  # example recovery script
```

### 6. Post-incident
- Update `transparency_roots` with new Merkle root after recovery
- File an incident report
- Add a regression test covering the specific failure mode

## See also
- `services/audit/writer.py`
- `sdk/common/merkle.py`
