# ADR-001: Cryptographic audit chain — DB trigger + Merkle + public S3 mirror

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: audit, compliance, transparency, soc2, eu-ai-act

## Context

Aegis sells **tamper-evidence as the moat**. Customers — regulated banks,
healthcare buyers, EU AI Act-impacted enterprises — need to prove to *their*
auditor that an AI agent's decisions were governed at the time they
happened, and that the audit record cannot have been edited after the
fact (by us, by them, or by an insider).

Two failure modes drive the design:

1. **DBA-with-root edits a row** to hide a bad decision.
2. **Aegis itself is compromised** (signing key stolen, S3 bucket rewritten).

Either failure must be detectable by a third party who never trusts Aegis
or its customer. "Trust us, we have logs" is not a credible enterprise
answer in 2026.

The most-cited prior incidents driving this requirement:
- Wells Fargo 2016 (3.5M fake accounts; insider deleted email evidence).
- Volkswagen Dieselgate (engineers edited test logs).
- Several EU AI Act §12 fines projected for late 2026 onward.

## Decision

We will implement a **three-layer audit chain**:

1. **Storage-layer immutability**: a Postgres trigger
   (`deny_audit_log_mutation`) on `audit_logs` fires `BEFORE UPDATE OR
   DELETE` for each row, raising SQLSTATE `P0001`
   (`services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:36-55`).
   The application database role cannot mutate a row regardless of code
   path. Dropping the trigger is itself a logged DDL event audited via
   RDS Database Activity Streams.

2. **Application-layer chaining**: each row carries `prev_hash` linking to
   the previous row in its 16-shard hash chain
   (`services/audit/alembic/versions/e9f1a2b3c4d5_add_chain_shard.py`).
   A `chain_sequence BIGINT IDENTITY` column gives a monotonic ordering
   even across partitions.

3. **Public transparency mirror**: daily per-tenant Merkle roots are
   computed, signed with **ed25519** (per-tenant CMK in AWS KMS), and
   uploaded to `s3://aegis-public-roots-628478946931/` — anonymous reads,
   no AWS credentials required. Each root carries `prev_root_hash`
   chaining back to genesis
   (`services/audit/transparency.py:97-137`,
    `services/audit/alembic/versions/g2h3i4j5k6l7_add_prev_root_hash.py`).
   The `aegis-verify` CLI (`pip install aegis-aevf`) walks the chain
   offline against any tenant's history.

## Alternatives considered

1. **App-only chaining (no DB trigger).** Cheaper to operate; no schema
   coupling. Rejected — a DBA edit invalidates the chain *and* the
   verifier has no way to distinguish "edit happened" from "row was
   always this value", because the prev_hash gets recomputed on the
   mutated value. The trigger is the only thing that forces tamper
   evidence at storage layer.
2. **External append-only log (Loki, S3 with Object Lock COMPLIANCE
   mode).** Considered for layer 1. Rejected for the *primary* log
   because query performance for SOC2 evidence pulls would be poor and
   joining to non-audit tables breaks. Adopted for layer 3 (public
   transparency mirror) where read patterns are scan-only.
3. **Blockchain anchor (Sigstore Rekor / Bitcoin OP_RETURN).** Considered
   but rejected as a *replacement* for S3 mirror — operationally
   complex, no customer asked for it, and S3 + `prev_root_hash` chain
   already gives detectability without a third-party time-anchor.
   Reserved as a possible *additional* layer if a single regulated
   customer demands public-blockchain anchoring (not on roadmap).
4. **RSA-2048 signatures.** Rejected vs ed25519 — ed25519 is faster, has
   smaller signatures, and is what Sigstore/Rekor and SSH-CA have
   converged on. No customer-cited compliance reason to prefer RSA at
   our trust tier.
5. **Single global signing key.** Rejected vs per-tenant CMK. Per-tenant
   isolation lets a customer revoke our access to their key (and
   therefore stop signing their own root) without affecting other
   tenants — a property regulated buyers ask for explicitly.

## Consequences

* **Positive**
  - DBA-with-root cannot edit a row without leaving a logged DDL event.
  - A customer who has archived even one historical daily root can
    detect a history rewrite by any future Aegis operator (including
    Aegis itself).
  - SOC2 CC7.2 + EU AI Act §12 + India DPDP §8(5) all map directly to
    audit-chain controls — saves us writing custom narratives per
    framework.
  - Public S3 mirror is operational evidence ("look it up yourself, no
    creds needed") that closes "trust us" objections in sales calls.
* **Negative**
  - Application code paths that *legitimately* need to fix a typo in an
    audit row (e.g., GDPR Article 17 erasure) must do so via a separate
    `redaction_record` row — the original audit row cannot be edited.
    Operator workflow lives in `docs/runbooks/tenant_data_request.md`.
  - Cost: per-tenant KMS CMK + S3 PUT per day per tenant per partition.
    Negligible at current scale; will revisit at 1k+ tenants.
  - Cryptographic-key rotation is a real operational burden — handled by
    `docs/runbooks/secrets_rotation.md` and the historical-keys table
    (`services/audit/alembic/versions/h3i4j5k6l7m8_add_transparency_columns_and_historical_keys.py`)
    that keeps old receipts verifiable after key swap.
* **Reversibility**
  - **Storage layer (trigger)**: trivial to drop, but doing so would
    constitute a fundamental product change customers were sold on.
  - **App-layer chaining**: 1-week migration to remove or restructure.
  - **Public transparency mirror**: trivial to stop new writes; *cannot*
    un-publish prior days — buyers have archived them.

## Implementation references

* `services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py` — DB trigger
* `services/audit/alembic/versions/e9f1a2b3c4d5_add_chain_shard.py` — 16-shard hash chain
* `services/audit/alembic/versions/g2h3i4j5k6l7_add_prev_root_hash.py` — root chain pointer
* `services/audit/transparency.py` — Merkle root + ed25519 signing
* `services/audit/public_transparency.py` — S3 upload
* `services/audit/transparency_scheduler.py` — daily root job
* `tools/aegis_verify/` — public CLI (PyPI `aegis-aevf`)
* `docs/AEVF/spec.md` — wire format spec (aevf/0.1.0)
* `docs/AEVF/auditor-checklist.md` — V1–V6 verification checklist
* `docs/runbooks/secrets_rotation.md` — key rotation
* `tests/test_audit_chain_verifier.py`, `tests/test_audit_chain_properties.py` — guard tests

## Verification

External auditor reproducing the chain in under 5 minutes:

```bash
# 1. Confirm the storage-layer trigger is alive in prod.
PGPASSWORD=$DB_PASS psql -h $AUDIT_HOST -U aegis -d acp_audit -c \
  "SELECT tgname FROM pg_trigger WHERE tgname = 'deny_audit_log_mutation';"
# expect: 1 row.

# 2. Pull a historical daily root anonymously.
aws s3 cp s3://aegis-public-roots-628478946931/roots/<tenant-uuid>/2026-06-18.json - \
  --no-sign-request

# 3. Walk the prev_root_hash chain back to genesis with the public CLI.
pip install aegis-aevf
aegis-verify --bucket aegis-public-roots-628478946931 --tenant <tenant-uuid>
# expect: V1–V6 PASS, n daily roots verified.
```

If any of those three steps fails, the chain has been broken — page
on-call immediately per `docs/runbooks/audit_chain_violation.md`.
