# Cryptographic Audit Chain

*The database append-only trigger, the signing scheme, the prev-hash chain, the daily Merkle root, the public S3 mirror, and the verification algorithm. Every other security claim in Aegis is anchored on this one.*

> **The chain format and verification algorithm described on this page are
> published as the open standard [AEVF (Aegis Evidence Verification Format)
> `aevf/0.1.0`](../AEVF/README.md).** The reference verifier ships on PyPI as
> `pip install aegis-aevf` and runs entirely offline — no Aegis account, no
> API key, no network call. This page is the *internal* description of how
> rows are produced; the AEVF spec is the *external* contract for verifying
> them. The two MUST stay in lock-step; the parity test
> `tools/aegis_verify/tests/test_no_network.py` enforces this.

## The three-layer defence

Tamper-evidence in Aegis is **defence in depth**, not a single mechanism.
Earlier revisions of this page described only the Merkle chain + S3 mirror;
that is stale. As of today the audit log is protected by **three independent
layers**, each anchored in a different trust domain:

| # | Layer | Lives in | Trust domain it defends |
|---|---|---|---|
| 1 | **Database append-only trigger** | Postgres `INSTEAD OF UPDATE/DELETE` on `audit_logs` | DB / DBA — any direct SQL `UPDATE` or `DELETE` raises `P0001: audit_logs is append-only; UPDATE is forbidden` and aborts the transaction. |
| 2 | **ed25519-signed Merkle root** | `services/audit/transparency.py` | Application — the daily transparency-sealing job chains every row via `prev_hash` + `event_hash`, builds the Merkle root, and signs it with the ed25519 transparency key. |
| 3 | **Public S3 mirror** | `s3://aegis-public-roots-628478946931/<date>.json` | Operator / network — every daily root is anonymously fetchable. Any third party who archived an earlier root can recompute the Merkle tree over their own export and verify the signature against the published public key. |

A compromise of **any one layer** still leaves the other two intact:

- An attacker with DB-superuser access who somehow bypasses the trigger
  (e.g. by re-creating the table) still has to forge a signed daily root
  that matches the public S3 mirror — and that mirror has already been
  archived by every customer who downloaded yesterday's `<date>.json`.
- An attacker who controls the ed25519 signing key still leaves the
  trigger's `P0001` error in Postgres logs the moment they try to overwrite
  a row, and still cannot rewrite the S3 mirror copies that customers
  already hold offline.
- An attacker who controls the S3 bucket cannot fake the chain because the
  trigger blocks tampering in the source DB, and because the signatures
  embedded in earlier customer-archived mirrors don't match the doctored
  ones served from the bucket today.

The rest of this page walks each layer in detail, then describes the
verification algorithm and the auditor-side CLI that walks all three.

## What it guarantees

Four properties that together let a third party trust the audit log without trusting the platform:

1. **Append-only at the database.** Even an attacker with direct SQL access to `audit_logs` cannot `UPDATE` or `DELETE` rows — the Postgres trigger raises `P0001` and aborts the transaction.
2. **Authenticity.** Every audit row is signed (ed25519). A row that did not originate from the audit service cannot pass verification.
3. **Chain integrity.** Each row's `prev_hash` is the previous row's `event_hash`. Rewriting row N forces a rewrite of every subsequent row in the same shard.
4. **Tamper-evident archival.** Once a day, a Merkle tree is built over every leaf in the (tenant, date) window and the root is recorded with a chain link to the previous day's root, signed with ed25519, and mirrored to a public S3 bucket. A customer who archives the day's root can later detect any rewrite — including rewrites done with platform-level database access.

Together: the audit chain proves that the sequence of recorded decisions is the same sequence that was actually written, with cryptographic precision.

> **Three-layer defence on audit integrity.** The crypto chain described on this page is one of three deliberately-redundant layers. Layer 1 is a Postgres `INSTEAD OF UPDATE/DELETE` trigger on `audit_logs` (Alembic revision `3a519b48a6f2`) that aborts any mutation — even from a superuser — at the point of write. Layer 2 is the prev-hash chain and daily Merkle root described below, which makes any tamper that *did* get through Layer 1 mathematically detectable. Layer 3 is the S3 receipt mirror (`s3://acp-receipts-prod/{tenant_id}/{audit_id}.json`) plus the public daily-root archive at `s3://aegis-public-roots-…`, which means even a Postgres-wide rewrite is detectable by anyone who archived an earlier root. See [Audit Signal Reference](../services/audit-signal-reference.md) for what each `metadata.findings` entry on a deny row means.

## Layer 1 — Database append-only trigger

Source: `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py`.

The first layer lives in Postgres itself, below the audit service. The
migration installs `INSTEAD OF UPDATE` and `INSTEAD OF DELETE` triggers on
`audit_logs`. Any direct SQL `UPDATE` or `DELETE` — issued by the audit
service, by an operator with `psql`, or by a compromised application — is
intercepted at the database layer and aborts the transaction with:

```text
ERROR:  audit_logs is append-only; UPDATE is forbidden
ERRCODE: P0001 (raise_exception)
```

The trigger is enforced for **all roles**, including the audit-service
role, the migration role, and any superuser session. The only way to
remove rows is to drop and re-create the table — a schema-level operation
that itself appears in Postgres logs and breaks every downstream
verifier (Merkle root, S3 mirror, customer-side AEVF check).

This layer was verified live during round-3 testing
(see [Testing](../../testing.md) → *round 3, trigger probe via asyncpg*).
The probe connects as the audit-service role, issues
`UPDATE audit_logs SET decision = 'allow' WHERE id = $1;`, and asserts the
driver raises `asyncpg.exceptions.RaiseError` with the expected message.

**Why this matters.** Before layer 1 existed, a database-compromising
attacker could silently overwrite or delete rows and the only evidence
would be a downstream Merkle-root mismatch — visible to a customer with an
archived prior root, but not to the operator. With the trigger in place,
the very first SQL that tries to overwrite a row produces an immediate
error visible in Postgres logs **and** is intercepted before the row
mutation reaches disk.

## Layer 2 — ed25519-signed Merkle root

The next two sections describe layer 2: how each row is signed and chained
in the application, and how the per-day Merkle root is sealed.

### The data shape

Source: `services/audit/signer.py` (top docstring) and `services/audit/models.py::AuditLog`.

Each row in `audit_logs` carries:

| Column | Purpose |
|---|---|
| `id` | UUID — the audit row identifier |
| `tenant_id` | UUID — the tenant scope; chain is per-tenant, per-shard |
| `agent_id`, `tool`, `action`, `decision`, `findings`, `metadata_json` | the decision content. `metadata_json.findings` carries the canonical signal IDs (e.g. `SEC-CUMULATIVE-Q1`, `FIN-WIRE-EXTERNAL-CAP`) that triggered the decision. The full catalogue lives in the [Audit Signal Reference](../services/audit-signal-reference.md). |
| `event_hash` | SHA-256 hex64 — this row's hash |
| `prev_hash` | SHA-256 hex64 — the previous row's `event_hash` (or `GENESIS_HASH` for the first row) |
| `signature` | base64url ed25519 signature over the canonical receipt JSON |
| `key_fingerprint` | SHA-256 of the public key that signed |
| `chain_shard` | int — per-(tenant, day) shard so concurrent writers don't serialize globally |
| `created_at` | timestamp |

### The canonical receipt format

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

### How a row is written

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

### How verification works

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

### The daily Merkle root

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

### The chain-of-roots

Source: `services/audit/transparency.py`.

Each daily root carries a `prev_root_hash` pointing at the previous day's root. The result is a chain of roots, one per day per tenant, that grows without bound.

Combined with layer 3's public mirror, this is one of the strongest guarantees Aegis offers: even if every Aegis-controlled secret is compromised in the future, any party who archived an earlier root can mathematically detect a rewrite of any row between the archived day and the present.

## Layer 3 — Public S3 mirror

Source: `services/audit/transparency_scheduler.py` → `_publish_root_to_public_mirror`.

Once a daily root is signed (layer 2), the transparency scheduler writes a
canonical JSON copy to a **publicly readable** S3 bucket:

```text
s3://aegis-public-roots-628478946931/<YYYY-MM-DD>.json
```

The bucket policy is `s3:GetObject` allowed for `Principal: "*"`. Anyone
on the public internet can fetch any past root anonymously:

```bash
aws s3 cp \
  s3://aegis-public-roots-628478946931/2026-06-17.json \
  - --no-sign-request
```

Each file carries the same fields recorded in `transparency_log_roots`:

```json
{
  "tenant_id":               "<uuid>",
  "date":                    "2026-06-17",
  "merkle_root":             "<hex64>",
  "prev_root_hash":          "<hex64>",
  "leaf_count":              2339,
  "leaf_range":              {"first": "<audit_id>", "last": "<audit_id>"},
  "signing_key_fingerprint": "<hex64>",
  "signature":               "<base64url-ed25519>",
  "window_end":              "2026-06-17T23:59:59Z"
}
```

The ed25519 **public** key used to verify these signatures is itself
published at `s3://aegis-public-roots-628478946931/keys/<fingerprint>.pem`
and rotated keys are retained indefinitely so old roots verify forever.

**Why a public bucket matters.** It moves the trust anchor *outside*
the Aegis trust boundary. A customer who runs a nightly cron of
`aws s3 cp` builds an offline archive of signed roots. Even total
compromise of every Aegis-controlled secret cannot rewrite history
across that customer's archive — every root they hold offline is a
notarised checkpoint against which a future `audit_logs` export can be
recomputed and verified.

This is why the page's earlier guarantees use "third-party verifiable"
rather than "Aegis-asserted" language: layer 3 is the layer that turns
the chain from a vendor claim into a verifiable mathematical artefact.

## Key storage and rotation

Source: `services/audit/signer.py` + `sdk/common/signing_keys.py` (Sprint 1.3).

Pre-Sprint-1 deployments read the signing key directly from
`/data/keys/receipt-signing.pem` on the audit container's filesystem — the
same blast radius as the database. The audit (C9 / S5) called this out as
incompatible with a tamper-evident claim: a database-compromising attacker
also has the key.

Sprint 1.3 introduces a `SigningKeyProvider` abstraction. Three providers ship:

| Provider | Storage | Recommended for |
|---|---|---|
| `SsmSigningKeyProvider` | AWS Systems Manager Parameter Store (SecureString, KMS-encrypted at rest) | **production** |
| `AwsKmsSigningKeyProvider` | KMS-wrapped blob (envelope encryption) in env / S3 | production when the PEM is too large for SSM |
| `LocalFileSigningKeyProvider` | PEM on disk / env var | dev only |

Env-var selection:

```bash
# Production default — SSM SecureString parameter under /aegis-audit/
RECEIPT_SIGNING_PROVIDER=ssm
RECEIPT_SIGNING_SSM_PARAMETER=/aegis-audit/receipt-signing-key
AWS_REGION=ap-south-1

# Independent root-signing key
ROOT_SIGNING_PROVIDER=ssm
ROOT_SIGNING_SSM_PARAMETER=/aegis-audit/root-signing-key

# Optional KMS envelope-encryption alternative (for PEMs > 4 KB or shared
# blobs in S3). The reference deployment provisions a customer CMK at
# alias/aegis-audit-envelope (ap-south-1) with annual rotation enabled.
# RECEIPT_SIGNING_PROVIDER=kms
# RECEIPT_SIGNING_KMS_KEY_ID=alias/aegis-audit-envelope
# RECEIPT_SIGNING_KMS_CIPHERTEXT_B64=<base64 of kms.Encrypt(PEM)>
```

The audit service calls `ssm:GetParameter(WithDecryption=True)` once at boot.
The plaintext PEM exists only in process memory; CloudTrail records every
access. Rotation is one `ssm:PutParameter` call plus the historical-key
promotion ritual described below — no application restart required.

Required IAM on the audit service's role: `ssm:GetParameter` on the
parameter ARN and `kms:Decrypt` on the CMK encrypting the SecureString
(typically `alias/aws/ssm`). Full operator walkthrough at
[Key Rotation](../operations/key-rotation.md#sprint-13-ssm-parameter-store-path).

The corresponding public key is recorded on every row as `key_fingerprint`.
When the operator rotates, the old key's fingerprint is promoted to
`transparency_historical_keys` BEFORE any row is written with the new key.
A row signed by key K verifies against either the current key or any row in
`transparency_historical_keys` with fingerprint K. Old receipts continue to
verify after rotation.

## Offline verifier (Sprint 1.1)

Source: `sdk/acp_client/verifier.py`, `sdk/acp_client/cli.py`.

Pre-Sprint-1, `acp verify chain <dir>` walked only the daily-root chain
(`root → prev_root → …`) and reported "chain consistent" without ever
checking per-receipt signatures, the shard-internal `prev_hash` linkage, or
the `event_hash` recomputation. The audit (C9) flagged this — a single
tampered receipt would slip through unnoticed.

After Sprint 1.1 the CLI runs **five independent checks** when pointed at an
export bundle (each check validates a different invariant of layer 2 — see
the three-layer table at the top of this page for the broader trust split):

1. **ed25519 signature verification** against active + historical public keys
   from `keys/active.pem` and `keys/historical/*.pem`.
2. **Merkle inclusion proof verification** for each receipt against its
   day's signed root.
3. **Shard `prev_hash` walk** — groups receipts by `(tenant_id, chain_shard)`,
   sorts by timestamp, asserts each row's `prev_hash` matches the previous
   row's `event_hash` (or `GENESIS_HASH` for the first row).
4. **Independent `event_hash` recomputation** — computes
   `sha256(prev_hash + canonical(business_fields))` and compares to the
   claimed `event_hash`. Catches in-place tamper even when the attacker
   controls the signing key.
5. **Daily-root chain consistency** — the legacy `root → prev_root` walk.

Operator usage:

```bash
# Full verification of an export bundle (the default).
acp verify chain ./my-export

# Roots-only walk (back-compat with the pre-Sprint-1 behavior).
acp verify chain ./my-export --roots-only
```

Adversarial tests in `tests/test_audit_chain_verifier.py` prove the verifier
rejects: a flipped byte in `event_hash`, a re-signed tamper where the
attacker controls the signing key, a deleted middle row, a swapped signing
key, an unanchored tail, and a wrong `GENESIS_HASH` on the first row.

## Auditor-side: the `aegis-verify` CLI

Source: the `aegis-aevf` package on PyPI (published 2026-06-14). See the
canonical [AEVF spec](../AEVF/spec.md) for the byte-precise algorithm.

`acp verify` (above) is the **operator-facing** CLI shipped inside the
Aegis SDK — it assumes the operator already has the SDK installed and
authenticated. `aegis-verify` is the **auditor-facing** equivalent: a
standalone, network-free, single-binary check that any third-party
auditor can run against an evidence bundle without any Aegis credentials.

Install:

```bash
pip install aegis-aevf
```

The package installs a single executable, `aegis-verify`, plus the AEVF
reference verifier as a Python library (`from aevf import verify_bundle`).

Run against an evidence bundle:

```bash
aegis-verify --bundle path/to/evidence.zip
```

The bundle is the export produced by `acp export evidence` or by the
[Evidence Export](../ui/primary/evidence-export.md) UI. It contains:

- All audit receipts in the requested date range
- The signed daily roots that cover them (layer 2)
- The active and historical ed25519 public keys
- A copy of each day's S3 mirror file (layer 3)

`aegis-verify` runs the same five layers as `acp verify chain` (signature,
Merkle inclusion, shard `prev_hash`, `event_hash` recomputation,
root-chain consistency) **plus** a sixth: cross-check every root in the
bundle against the publicly mirrored copy at
`s3://aegis-public-roots-628478946931/<date>.json`. The S3 check is
*opt-in* (`--check-public-mirror`) because some auditors run in
air-gapped environments. When enabled it confirms layer 3: that what's
in the bundle is byte-for-byte what was published at the time.

Exit code is `0` on success, non-zero on any failure. The CLI prints a
human-readable summary of the receipts, roots, and key fingerprints
verified. Sample success line:

```text
aegis-verify: OK — verified 2,339 receipts across 1 day, 1 shard, 1 key fingerprint
              (layers: signature ed25519, merkle, shard-prev-hash, event-hash
               recomputation, root-chain, public-mirror cross-check)
```

The parity test in `tools/aegis_verify/tests/test_no_network.py` runs
`aegis-verify` against the reference bundle with the network stack
mocked offline — any silent dependency on Aegis-side endpoints breaks
the test and blocks the release.

## Live-tail anchoring (Sprint 1.2)

Source: `services/audit/transparency_scheduler.py` + `transparency.py::_sign_root`.

Pre-Sprint-1.2 the transparency scheduler defaulted to an hourly cadence and
the audit (C9) flagged a 24-hour truncation window: a row written at
00:01 UTC was only committed to a sealed Merkle root at midnight the next
day, so a database-compromising attacker had up to 24 h to silently delete
today's tail.

Sprint 1.2 closes this:

- Scheduler default cadence dropped from 3600s to **30s**
  (`TRANSPARENCY_SCHEDULER_INTERVAL`).
- Signed root payload now carries `window_end` — the precise UTC instant
  the root committed to. For today's running root this advances on every
  pass; for closed days it pins to end-of-day UTC.
- The offline verifier reads `window_end` and flags any receipt whose
  timestamp is past the most-recent signed anchor as an "unanchored tail."

Net effect: a truncation attack on today's tail is detectable within
seconds, not 24 hours. The closed-day semantics are unchanged — pre-1.2
roots without `window_end` are treated as anchoring end-of-day so historical
exports continue to verify cleanly.

## Worked example

Suppose a tenant has three rows in shard 0 with hashes A, B, C produced in that order.

- Row 1: `prev_hash = GENESIS_HASH`, `event_hash = A`.
- Row 2: `prev_hash = A`, `event_hash = B`.
- Row 3: `prev_hash = B`, `event_hash = C`.

To tamper with row 2's content the attacker must defeat all three layers:

**Layer 1 — DB trigger.** The attacker connects to Postgres and issues
`UPDATE audit_logs SET decision = 'allow' WHERE id = $row2_id;`. The
`INSTEAD OF UPDATE` trigger raises `P0001: audit_logs is append-only;
UPDATE is forbidden` and aborts the transaction. To proceed at all the
attacker must `DROP TRIGGER` (or `DROP TABLE` and re-create), both of
which appear in Postgres logs and require schema-level privilege.

**Layer 2 — signature + chain.** Suppose the attacker is privileged
enough to drop the trigger.

- They compute a new canonical JSON for row 2 with different fields.
- Re-signing with the original ed25519 key requires the private key,
  which is not in the database. Suppose the attacker has it as well.
- The new `event_hash` for row 2 is `B'` ≠ `B`.
- Row 3's `prev_hash` is still `B`, but the chain integrity check expects
  `B'`. Verification fails.

To make the chain consistent, the attacker must also rewrite row 3 (set
`prev_hash = B'`, compute new `event_hash = C'`) and re-sign. And row 4.
And every subsequent row. And the daily Merkle root, which now references
the original `B` somewhere in the tree.

To make the daily root consistent, the attacker must rewrite the root and
break the chain to the previous day's root via `prev_root_hash`.

**Layer 3 — public S3 mirror.** Even after rewriting everything in the
database, the attacker has not touched the copies of past daily roots
already downloaded by customers from
`s3://aegis-public-roots-628478946931/`. Any customer holding an earlier
mirrored root can verify the day's root content against the recomputed
Merkle tree and detect the change. The signed root they hold offline is
mathematically incompatible with the freshly forged chain.

Net effect: silent tamper requires defeating Postgres triggers,
the signing key, the per-tenant Merkle chain, the day-over-day
`prev_root_hash` chain, **and** every customer-archived copy of every
past root simultaneously.

## Verification at the API

Two endpoints surface the chain:

- `GET /audit/logs/verify` — runs `verify_audit_chain` for the tenant and returns `{valid, violations, rows_checked}`. The Audit Trail UI calls this every 30 seconds.
- `GET /audit/logs/{id}/receipt` — returns the canonical receipt for one row plus the inclusion proof for the day's root.
- `POST /receipts/verify` — accepts an externally-archived receipt and verifies it against the live chain (or against historical keys if the receipt was issued under a rotated key).
- `POST /transparency/verify-root` — accepts an externally-archived root and verifies it against the live `transparency_roots` table.

A response of `{ "valid": true, "violations": [] }` is the only acceptable healthy state.

## What it does NOT guarantee

- **Confidentiality of audit content.** The receipts are signed, not encrypted. Any holder of a receipt sees its content. Audit content is intentionally readable so it can be processed by compliance tools.
- **Protection against schema-level destruction.** Layer 1's trigger blocks `UPDATE` and `DELETE` for **every role**, but an attacker with `DROP TABLE` or `TRUNCATE` privilege can still destroy `audit_logs` wholesale. That destruction is non-stealthy (Postgres logs it, downstream Merkle and S3 verification fail loudly, and `pg_dump` snapshots survive) but the trigger does not by itself prevent it. Restrict `DROP`/`TRUNCATE` at the role layer and keep the nightly `pg_dump` to S3 — see [Backup & Restore](../operations/backup-restore.md).
- **Recovery from total chain loss.** If the rows are destroyed entirely (rather than tampered), verification has nothing to compare against. The S3 receipt store, the public root mirror (layer 3 — see above), and nightly `pg_dump` are the recovery path; see [Backup & Restore](../operations/backup-restore.md).

## Next

- [AEVF Overview](../AEVF/README.md) — the open standard published from this same construction (auditor-facing entry page)
- [AEVF Spec](../AEVF/spec.md) — the byte-precise verification algorithm (V1–V6)
- [Auditor Checklist](../AEVF/auditor-checklist.md) — 8-section reviewable checklist external auditors sign off against
- `pip install aegis-aevf` — the [PyPI package](https://pypi.org/project/aegis-aevf/) that ships `aegis-verify` for third-party auditors (see above)
- [Audit Signal Reference](../services/audit-signal-reference.md) — the canonical signal IDs that flow through `metadata.findings`
- [Audit service](../services/audit.md) — implementation and ops detail
- [Key Rotation](../operations/key-rotation.md) — the operator runbook
- [Audit Chain Violation runbook](../operations/runbooks/audit-chain-violation.md) — what to do when verify fails
- [Audit Trail UI](../ui/primary/audit-trail.md) — the human-facing surface
- [Testing](../testing.md) — see round 3 for the live trigger probe and end-to-end verification of all three layers
