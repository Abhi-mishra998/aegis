# Cryptographic Audit Chain

*The signing scheme, the prev-hash chain, the daily Merkle root, and the verification algorithm. Every other security claim in Aegis is anchored on this one.*

## What it guarantees

Three properties that together let a third party trust the audit log without trusting the platform:

1. **Authenticity.** Every audit row is signed (ed25519). A row that did not originate from the audit service cannot pass verification.
2. **Chain integrity.** Each row's `prev_hash` is the previous row's `event_hash`. Rewriting row N forces a rewrite of every subsequent row in the same shard.
3. **Tamper-evident archival.** Once a day, a Merkle tree is built over every leaf in the (tenant, date) window and the root is recorded with a chain link to the previous day's root. A customer who archives the day's root can later detect any rewrite — including rewrites done with platform-level database access.

Together: the audit chain proves that the sequence of recorded decisions is the same sequence that was actually written, with cryptographic precision.

## The data shape

Source: `services/audit/signer.py` (top docstring) and `services/audit/models.py::AuditLog`.

Each row in `audit_logs` carries:

| Column | Purpose |
|---|---|
| `id` | UUID — the audit row identifier |
| `tenant_id` | UUID — the tenant scope; chain is per-tenant, per-shard |
| `agent_id`, `tool`, `action`, `decision`, `findings`, `metadata_json` | the decision content |
| `event_hash` | SHA-256 hex64 — this row's hash |
| `prev_hash` | SHA-256 hex64 — the previous row's `event_hash` (or `GENESIS_HASH` for the first row) |
| `signature` | base64url ed25519 signature over the canonical receipt JSON |
| `key_fingerprint` | SHA-256 of the public key that signed |
| `chain_shard` | int — per-(tenant, day) shard so concurrent writers don't serialize globally |
| `created_at` | timestamp |

## The canonical receipt format

Source: `services/audit/signer.py:7-23`.

```json
{
  "version":      1,
  "execution_id": "<audit row id>",
  "tenant_id":    "<uuid>",
  "agent_id":     "<uuid>",
  "tool":         "<string|null>",
  "action":       "<string>",
  "decision":     "allow|deny|error",
  "reason":       "<string|null>",
  "request_id":   "<string|null>",
  "timestamp":    "<ISO-8601>",
  "event_hash":   "<hex64|null>",
  "prev_hash":    "<hex64|null>",
  "chain_shard":  "<int>"
}
```

Conventions:

- Canonical JSON: keys sorted, no whitespace.
- Signature: ed25519 over the UTF-8 bytes of the canonical JSON.
- Signature encoding: base64 url-safe, no padding.

The point of a canonical JSON is reproducibility. Two parties that have the same row content compute the same byte string and verify the same signature.

## How a row is written

Source: `services/audit/outbox_worker.py` plus `services/audit/writer.py`.

1. Gateway emits the event to the Redis stream `acp:audit_events`.
2. Audit worker `XREADGROUP`s the event.
3. Acquires `acp:audit_chain_lock:{tenant_id}` (SETNX with 5-second TTL) — serializes chain writes per tenant.
4. Reads previous `event_hash` from `acp:audit_chain_tail:{tenant_id}` (Redis-cached) or from Postgres.
5. Computes canonical content hash: SHA-256 over the canonical JSON.
6. Computes chained `event_hash`: `SHA-256(prev_event_hash || canonical_hash)`.
7. Signs the event_hash with ed25519 using today's signing key.
8. Atomic transaction: `INSERT audit_logs` plus `INSERT pending_usage_events`.
9. Updates `acp:audit_chain_tail:{tenant_id}` with the new event_hash.
10. `XACK acp:audit_events`.

The chain lock is per-tenant, not platform-wide. Different tenants can write concurrently. Within a tenant, the lock serializes so the prev_hash never goes stale mid-write.

## How verification works

Source: `services/audit/integrity.py::verify_audit_chain`.

For a given `tenant_id`:

1. Load all rows for the tenant, ordered by `(chain_shard ASC, timestamp ASC, id ASC)`.
2. For each row:
   - Look up `expected_prev = last_verified_hash.get(shard, GENESIS_HASH)`.
   - Compute `recomputed = compute_event_hash(prev_hash=str(entry.prev_hash or GENESIS_HASH), tenant_id=str(entry.tenant_id), ...)`.
   - Compare `recomputed` to `entry.event_hash`. Mismatch → tamper detected.
   - Compare `entry.prev_hash` to `expected_prev`. Mismatch → chain break.
3. Verify the signature against the current public key OR any retired key in `transparency_historical_keys` (so key rotation does not invalidate older receipts).
4. Update `last_verified_hash[shard]` to the row's `event_hash`.

The two checks together catch:

- Silent row content modification (recompute fails).
- Insert in the middle (prev_hash chain break).
- Delete (next row's prev_hash no longer matches).
- Reorder (prev_hash chain break).

Source for the empty-chain convention: lines 76–90 of `services/audit/integrity.py`. An empty chain is integrous by definition; previously this returned `success=True` without an `is_integrous` field, which collapsed to "broken" in the UI's truthiness check. Fixed.

## The daily Merkle root

Source: `services/audit/merkle.py` and `services/audit/transparency.py`.

Once per day, the transparency scheduler:

1. Collects every `event_hash` for the day, partitioned by tenant.
2. Sorts leaves by `(timestamp ASC, audit_id ASC)` so two implementations produce the same tree.
3. Builds the Merkle tree:
   - Inner nodes: `H(left || right)` with SHA-256.
   - Odd level: duplicate the last node (Bitcoin convention).
   - Empty tree: root = `sha256(b"")` (zero-leaf sentinel).
4. Records the root in `transparency_roots` with:
   - `tenant_id`, `date`, `merkle_root`, `leaf_count`, `leaf_range`.
   - `prev_root_hash` — the previous day's root for this tenant, forming a day-over-day chain.
   - `signing_key_fingerprint` and a signature over the root content.

The inclusion proof shape (from the `merkle.py` docstring):

```json
{
  "leaf":  "<hex32>",
  "index": <int>,
  "siblings": [
    {"side": "L"|"R", "hash": "<hex32>"},
    ...
  ],
  "root":  "<hex32>",
  "size":  <int>
}
```

A customer who archives the day's root can later verify any leaf's inclusion by walking the sibling chain back to the root.

## The chain-of-roots

Source: `services/audit/transparency.py`.

Each daily root carries a `prev_root_hash` pointing at the previous day's root. The result is a chain of roots, one per day per tenant, that grows without bound.

This is the strongest guarantee Aegis offers: even if every Aegis-controlled secret is compromised in the future, any party who archived an earlier root can mathematically detect a rewrite of any row between the archived day and the present.

## Key storage and rotation

Source: `services/audit/signer.py:42-55`.

Key precedence at load time:

1. **`RECEIPT_SIGNING_PRIVATE_KEY` env var** (base64 PEM) — preferred for production. The secret is mounted as an env var; no key on disk.
2. **`/data/keys/receipt-signing.pem`** on the audit container (persistent volume) — survives restarts.
3. **Generated fresh in memory with a log warning** — acceptable only in tests.

The corresponding public key is recorded on every row as `key_fingerprint`. When the operator rotates, the old key's fingerprint is promoted to `transparency_historical_keys` BEFORE any row is written with the new key. The runbook ([Key Rotation](../operations/key-rotation.md)) enforces this order.

A row signed by key K verifies against either the current key or any row in `transparency_historical_keys` with fingerprint K. Old receipts continue to verify after rotation.

## Worked example

Suppose a tenant has three rows in shard 0 with hashes A, B, C produced in that order.

- Row 1: `prev_hash = GENESIS_HASH`, `event_hash = A`.
- Row 2: `prev_hash = A`, `event_hash = B`.
- Row 3: `prev_hash = B`, `event_hash = C`.

To tamper with row 2's content:

- The attacker computes a new canonical JSON for row 2 with different fields.
- Re-signing with the original ed25519 key requires the private key, which is not in the database. Suppose the attacker has it.
- The new `event_hash` for row 2 is `B'` ≠ `B`.
- Row 3's `prev_hash` is still `B`, but the chain integrity check expects `B'`. Verification fails.

To make the chain consistent, the attacker must also rewrite row 3 (set `prev_hash = B'`, compute new `event_hash = C'`) and re-sign. And row 4. And every subsequent row. And the daily Merkle root, which now references the original `B` somewhere in the tree.

To make the daily root consistent, the attacker must rewrite the root and break the chain to the previous day's root via `prev_root_hash`.

Any customer holding an earlier `prev_root_hash` archive can verify the day's root content against the recomputed Merkle tree and detect the change.

## Verification at the API

Two endpoints surface the chain:

- `GET /audit/logs/verify` — runs `verify_audit_chain` for the tenant and returns `{valid, violations, rows_checked}`. The Audit Trail UI calls this every 30 seconds.
- `GET /audit/logs/{id}/receipt` — returns the canonical receipt for one row plus the inclusion proof for the day's root.
- `POST /receipts/verify` — accepts an externally-archived receipt and verifies it against the live chain (or against historical keys if the receipt was issued under a rotated key).
- `POST /transparency/verify-root` — accepts an externally-archived root and verifies it against the live `transparency_roots` table.

A response of `{ "valid": true, "violations": [] }` is the only acceptable healthy state.

## What it does NOT guarantee

- **Confidentiality of audit content.** The receipts are signed, not encrypted. Any holder of a receipt sees its content. Audit content is intentionally readable so it can be processed by compliance tools.
- **Real-time tamper alerting.** The verifier runs on the audit-trail page's 30-second poll plus a nightly cron. Active tampering by an attacker with database access could go undetected for up to one day before a customer-side archive comparison catches it.
- **Recovery from total chain loss.** If the rows are deleted entirely (rather than tampered), verification has nothing to compare against. The S3 receipt store and nightly `pg_dump` are the recovery path; see [Backup & Restore](../operations/backup-restore.md).

## Next

- [Audit service](../services/audit.md) — implementation and ops detail
- [Key Rotation](../operations/key-rotation.md) — the operator runbook
- [Audit Chain Violation runbook](../operations/runbooks/audit-chain-violation.md) — what to do when verify fails
- [Audit Trail UI](../ui/primary/audit-trail.md) — the human-facing surface
