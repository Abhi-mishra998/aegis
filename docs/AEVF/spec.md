# AEVF — Aegis Evidence Verification Format

**Version:** `aevf/0.1.0`
**Bundle format identifier:** `aegis-evidence-bundle/2026-06`
**Status:** Draft Open Standard
**License:** Apache License 2.0
**Editors:** Aegis Engineering
**Date:** 2026-06-14

> This document is the **complete, byte-precise specification** of an AEVF
> evidence bundle and the algorithm that verifies it. **A competent
> engineer with the cryptography primitives below — SHA-256, ed25519,
> URL-safe base64 — must be able to write an independent verifier from
> this document alone, without reading any Aegis source code.** If you
> find an ambiguity, that is a spec defect; please report it.

---

## Table of contents

1. [Why AEVF exists](#1-why-aevf-exists)
2. [Conformance terminology](#2-conformance-terminology)
3. [Cryptographic primitives](#3-cryptographic-primitives)
4. [Canonical JSON](#4-canonical-json)
5. [Bundle envelope](#5-bundle-envelope)
6. [Public keys + key rotation](#6-public-keys--key-rotation)
7. [Audit row + event_hash](#7-audit-row--event_hash)
8. [Per-shard prev_hash chain](#8-per-shard-prev_hash-chain)
9. [Merkle leaf + root construction](#9-merkle-leaf--root-construction)
10. [Empty-epoch roots](#10-empty-epoch-roots)
11. [Daily root signature + cross-day chain](#11-daily-root-signature--cross-day-chain)
12. [Inclusion proofs (optional but normative when present)](#12-inclusion-proofs)
13. [Retention metadata](#13-retention-metadata)
14. [Regulatory mappings](#14-regulatory-mappings)
15. [Verification algorithm (V1–V6)](#15-verification-algorithm-v1v6)
16. [Test vectors](#16-test-vectors)
17. [Versioning + forward compatibility](#17-versioning--forward-compatibility)
18. [Security considerations](#18-security-considerations)

---

## 1. Why AEVF exists

The product question this spec answers is one sentence:

> *"If the AI agent that touched our data disappeared from your servers tomorrow, and your company along with it, would my auditor still be able to verify our evidence?"*

A logging vendor cannot answer "yes" to that question. AEVF can. AEVF
defines a self-describing, vendor-neutral evidence bundle and the
verification algorithm over it. An auditor receives an AEVF bundle, runs
**any** AEVF-conformant verifier offline — no network, no vendor
account, no API key — and gets `PASS` or a named broken row.

This spec is **open** so that:

- An auditor can independently verify evidence with their own tools
- A second-source verifier can exist (and one will, written by an auditor)
- The format outlives any one vendor

The format is **not** a logging protocol. It is the persistence + proof
format for already-collected runtime decisions. AEVF says nothing about
how decisions are made, only how they are **provably recorded**.

---

## 2. Conformance terminology

The key words **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL
NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **MAY**, and
**OPTIONAL** in this document are to be interpreted as in RFC 2119 and
RFC 8174 when, and only when, they appear in all capitals.

A **conformant verifier** MUST implement checks V1 through V6 as
defined in section 15.

A **conformant producer** MUST emit bundles whose envelope, canonicalization,
hash, signature, and chain semantics match this document exactly.

---

## 3. Cryptographic primitives

| Primitive | Choice | Notes |
|---|---|---|
| Hash | SHA-256 (FIPS 180-4) | All hashes 32 bytes; serialized as **lowercase hex**. |
| Asymmetric signature | Ed25519 (RFC 8032) | 32-byte public key, 64-byte signature. |
| Public key encoding | PEM, SubjectPublicKeyInfo (RFC 5280 §4.1.2.7) | Each key SHALL be carried as a PEM string `-----BEGIN PUBLIC KEY-----…`. |
| Signature encoding | URL-safe base64 **without padding** (RFC 4648 §5) | Decoders MUST re-pad before decoding (`s + "=" * (-len(s) % 4)`). |
| Public-key fingerprint | First 16 bytes of `SHA-256(PEM)`, lowercase hex (32 hex chars) | Used as `kid`. |

A verifier MAY accept additional algorithms only behind a published
profile. Bundles using non-default primitives MUST declare them in the
`algorithm` field of the relevant section.

---

## 4. Canonical JSON

AEVF uses **two distinct canonical JSON forms**, distinguished only by
JSON `separators`. Implementations MUST treat the two as different
byte streams.

### 4.1 Compact canonical JSON (used for signatures)

```
canonical_compact(obj) :=
    json.dumps(obj,
               sort_keys=True,
               separators=(",", ":"),
               ensure_ascii=False).encode("utf-8")
```

- Keys sorted ASCII-lexicographically at every level.
- No whitespace between tokens (`{"a":1,"b":2}`, not `{"a": 1, "b": 2}`).
- UTF-8 encoded; non-ASCII characters preserved verbatim, not `\uXXXX` escaped.
- Numbers serialized as the producer's language emits them; integers are
  RECOMMENDED (no trailing `.0`).

### 4.2 Default canonical JSON (used for event_hash)

```
canonical_default(obj) :=
    json.dumps(obj, sort_keys=True).encode("utf-8")
```

- Keys sorted ASCII-lexicographically at every level.
- **Default Python `json.dumps` separators**: `(", ", ": ")` — note the
  spaces. **`{"a": 1, "b": 2}`**, not `{"a":1,"b":2}`.
- UTF-8 encoded.

> ⚠ **Implementer note.** The two-form design predates AEVF and is
> preserved for backward compatibility with bundles signed before this
> spec was published. A future v1.0.0 of AEVF MAY consolidate to a
> single form behind a `format_version` bump. For now, conformant
> implementations MUST distinguish the two by separators.

---

## 5. Bundle envelope

An AEVF bundle is a single JSON object (or a `.zip`/`.tar.gz` containing
one named `bundle.json`).

```jsonc
{
  "format_version": "aegis-evidence-bundle/2026-06",
  "framework":      "eu-ai-act",
  "tenant_id":      "00000000-0000-0000-0000-000000000001",
  "period":         { "start": "2026-06-01", "end": "2026-06-30" },
  "generated_at":   "2026-06-30T23:59:59Z",

  "public_keys":  [ /* §6 */ ],
  "merkle_roots": [ /* §11 */ ],
  "records":      [ /* §7 */ ],

  "retention_metadata": { /* §13 */ }
}
```

**Required fields:** `format_version`, `framework`, `tenant_id`,
`period`, `generated_at`, `public_keys`, `merkle_roots`, `records`.

**Optional fields:** `retention_metadata` (RECOMMENDED), `inclusion_proofs`
(see §12), `producer_metadata` (free-form, IGNORED by the verifier).

`format_version` MUST be one of the set of recognized formats the
verifier is built for. The current set is `{"aegis-evidence-bundle/2026-06"}`.

---

## 6. Public keys + key rotation

Every ed25519 public key that signed any artifact in this bundle MUST
appear in `bundle.public_keys`. The verifier MUST NOT fetch keys from
any external source — that would defeat "offline verification."

```jsonc
{
  "kid":       "<32 hex chars>",         // first 16 bytes of sha256(PEM), hex
  "algorithm": "ed25519",
  "pem":       "-----BEGIN PUBLIC KEY-----\nMCowB...\n-----END PUBLIC KEY-----\n",
  "valid_from": "2026-01-01T00:00:00Z",
  "valid_to":   null                     // null = currently valid
}
```

**Key rotation.** A new key is added to the bundle with its own `kid`. A
Merkle root signed under the old key continues to verify against the
old key in the bundle. A root signed under the new key references the
new `kid`. The verifier MUST select the key by exact `kid` match — it
MUST NOT fall back to any other key on `kid` mismatch.

**Fingerprint computation:**

```python
fingerprint(pem_bytes) := sha256(pem_bytes).hexdigest()[:32]
```

The hash is over the raw PEM bytes (including the BEGIN/END lines and
newlines), so two implementations using different PEM line-wrap widths
will produce different fingerprints. Producers SHOULD use 64-character
line wrap (the OpenSSL default).

---

## 7. Audit row + event_hash

Each `bundle.records[i]` carries one `audit_row` plus optional
`mappings` and a `merkle_root_date` back-reference.

```jsonc
{
  "audit_row": {
    "id":            "<uuid>",
    "tenant_id":     "<uuid>",
    "agent_id":      "<uuid>",
    "action":        "execute_tool",
    "tool":          "tool.sql_query",
    "decision":      "deny",
    "request_id":    "<uuid>",
    "event_hash":    "<sha256 hex>",
    "prev_hash":     "<sha256 hex>",
    "chain_shard":   0,
    "timestamp":     "2026-06-13T12:00:00.000000+00:00",
    "metadata_json": { /* opaque to AEVF; not part of event_hash */ }
  },
  "mappings":         { "eu_ai_act": ["Article 12"], "soc2": ["CC6.1"] },
  "merkle_root_date": "2026-06-13"
}
```

### 7.1 event_hash recipe (normative)

`event_hash` is computed over **six fields only**, in the canonical
default form (with spaces). Any drift produces a non-recomputable hash
and the verifier MUST reject the row.

```python
def compute_event_hash(prev_hash: str,
                       tenant_id: str, agent_id: str,
                       action: str, tool: str | None,
                       decision: str, request_id: str | None) -> str:
    payload = json.dumps(
        {
            "tenant_id":  str(tenant_id),
            "agent_id":   str(agent_id),
            "action":     str(action),
            "tool":       str(tool or ""),
            "decision":   str(decision),
            "request_id": str(request_id or ""),
        },
        sort_keys=True,   # default separators — spaces!
    )
    return sha256(f"{prev_hash}{payload}".encode("utf-8")).hexdigest()
```

Producers MUST pass `None`/`null` `tool` and `request_id` as the literal
string `""` (empty string) — NOT the JSON literal `null`.

### 7.2 The genesis hash

The first row in any `(tenant, shard)` chain MUST use
`prev_hash = "0" * 64` (sixty-four ASCII zeros).

---

## 8. Per-shard prev_hash chain

Rows form a hash chain inside each `chain_shard`. To improve write
throughput Aegis maintains 16 shards by default; the chain is
verified per-shard, not globally.

For each `shard` in the bundle:

1. Sort the rows in that shard by `(timestamp ASC, id ASC)`.
2. For every adjacent pair `(prev, cur)`: `cur.prev_hash` MUST equal
   `prev.event_hash`.
3. For the first row in the shard: `prev_hash` MUST equal
   `GENESIS_HASH = "0" * 64` OR be the `event_hash` of a row whose
   `id` matches the bundle's stated `leaf_range_start_id` for some
   prior root (see §11) — i.e. it continues a chain that started
   before this bundle.

Any deletion in the middle of a shard breaks adjacency and MUST be
flagged by check V3.

---

## 9. Merkle leaf + root construction

### 9.1 Leaf

For each audit row in a daily window, the producer signs a **receipt**
that wraps the row's content. The leaf is:

```python
leaf_hex(receipt) := sha256(canonical_compact(receipt)).hexdigest()
```

The receipt's exact shape is a producer concern; what AEVF requires is
that the producer publishes the canonical bytes that were hashed. The
verifier does not need to recompute leaves from the audit row — the
Merkle root signature in §11 commits to the leaves transitively.

### 9.2 Sorting

Leaves MUST be sorted by `(timestamp ASC, audit_id ASC)` of the row
they correspond to. Two conformant implementations sorting the same
leaf set MUST produce the same tree.

### 9.3 Inner nodes

```python
inner(left_32B, right_32B) := sha256(left_32B || right_32B).digest()
```

(32-byte raw concatenation, NOT hex.) The 32-byte digest output is the
inner node's value.

### 9.4 Odd levels

When a level has an odd number of nodes, the last node is **duplicated**
(Bitcoin-style). Producers and verifiers MUST agree on this rule.

### 9.5 Root encoding

The 32-byte root is serialized as lowercase hex (64 hex chars) in the
bundle's `merkle_roots[].root_hash` field.

### 9.6 Empty tree sentinel

If a `(tenant, day)` window has zero audit rows, `build_root([])` MUST
return:

```
EMPTY_ROOT = sha256(b"").hexdigest()
           = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
```

(See §10 for the empty-epoch root signature scheme, which differs
from this trivial empty-tree sentinel.)

---

## 10. Empty-epoch roots

When a tenant has no audit rows in a daily window, the producer MUST
still emit a Merkle root entry to keep the cross-day chain (§11) unbroken.
The root for an empty epoch is:

```python
empty_epoch_root(prev_root_hash: str | None) :=
    sha256(
        (prev_root_hash or "").encode("ascii")
        + b"\ntransparency_empty_epoch_v1\n"
    ).hexdigest()
```

The domain separator `transparency_empty_epoch_v1` ensures no legitimate
leaf-set tree can produce a colliding hash.

---

## 11. Daily root signature + cross-day chain

Each entry in `bundle.merkle_roots`:

```jsonc
{
  "root_date":           "2026-06-13",
  "root_hash":           "<sha256 hex>",
  "leaf_count":          42,
  "leaf_range_start_id": "<uuid of first leaf>",
  "leaf_range_end_id":   "<uuid of last leaf>",
  "prev_root_hash":      "<sha256 hex of previous day's root>" | null,
  "kid":                 "<key fingerprint>",
  "algorithm":           "ed25519",
  "signature_b64":       "<URL-safe base64, no padding>",
  "signed_payload_canonical_json": "<the exact bytes that were signed>"
}
```

### 11.1 What gets signed

The producer computes the canonical compact JSON (§4.1) of a `root_receipt`
object containing at minimum the fields `kind`, `version`, `root_date`,
`root_hash`, `tenant_id`, `leaf_count`, `leaf_range_start_id`,
`leaf_range_end_id`, `prev_root_hash`. The exact serialized bytes are
included in the bundle as `signed_payload_canonical_json` so the verifier
does not need to reconstruct them.

### 11.2 Verification

```python
pk = load_pem_public_key(public_keys[kid].pem.encode("utf-8"))
pk.verify(
    base64url_decode_no_padding(signature_b64),   # see §3
    signed_payload_canonical_json.encode("utf-8")
)
# Raises InvalidSignature on failure.
```

### 11.3 Cross-day chain

`merkle_roots[i].prev_root_hash` MUST equal
`merkle_roots[i-1].root_hash` after sorting roots by `root_date ASC`.

`prev_root_hash == null` is permitted ONLY for the first root in the
chain (a tenant's first transparency-log epoch). A `null` mid-chain
is a SPEC VIOLATION and MUST fail check V5.

---

## 12. Inclusion proofs

A bundle MAY include inclusion proofs to let an auditor verify a single
row against a published root without re-downloading all leaves. When
present, each proof has the shape:

```jsonc
{
  "leaf":  "<hex32>",
  "index": <int>,            // 0-based position in the sorted leaves
  "siblings": [
    { "side": "L"|"R", "hash": "<hex32>" }   // bottom-up
  ],
  "root":  "<hex32>",
  "size":  <int>             // total leaf count
}
```

Verifier:

```python
cur = unhex(proof.leaf)
for sib in proof.siblings:
    sh = unhex(sib.hash)
    cur = sha256(sh + cur) if sib.side == "L" else sha256(cur + sh)
return hex(cur) == proof.root
```

The verifier MUST also confirm `proof.root` matches the day's
`merkle_roots[].root_hash` for the date the leaf belongs to.

Inclusion proofs are OPTIONAL in v0.1.0. Verifiers MAY warn if proofs
are absent.

---

## 13. Retention metadata

```jsonc
"retention_metadata": {
  "policy":                     "6_months_minimum",
  "configured_retention_days":  180,
  "earliest_row_in_bundle":     "2025-12-15T08:14:21.000000+00:00",
  "latest_row_in_bundle":       "2026-06-13T23:59:59.999999+00:00"
}
```

A producer claiming a 6-month retention policy MUST NOT ship a bundle
whose `earliest_row_in_bundle` is more than `configured_retention_days`
old. (If it is older, the claim is dishonest — the row should have been
deleted by policy.)

This field is REQUIRED in `aegis-evidence-bundle/2026-06`.

---

## 14. Regulatory mappings

Each record MAY carry per-framework mappings:

```jsonc
"mappings": {
  "eu_ai_act":   ["Article 12", "Article 16"],
  "soc2":        ["CC6.1", "CC6.6"],
  "nist_ai_rmf": ["MEASURE 2.1"],
  "dpdp":        ["Section 8(5) — security safeguards"]
}
```

AEVF defines the mapping field, not the canonical list of control IDs.
Producers SHOULD use the official control-ID strings as published by
each framework. Verifiers MUST NOT reject a bundle on the grounds that
a mapping is missing or unrecognized — mappings are evidence, not
constraints.

---

## 15. Verification algorithm (V1–V6)

A conformant verifier MUST implement all six checks. A bundle PASSES
only if every check passes; on any failure the report MUST name the
first broken artifact (row id, root date, or key kid) and SHOULD
continue checking remaining artifacts.

### V1 — Bundle format recognized

```
fmt = bundle["format_version"]
assert fmt in SUPPORTED_FORMATS, f"unknown bundle format {fmt!r}"
```

### V2 — Per-row event_hash recompute

For every row in `bundle.records[*].audit_row`:

```
recomputed = compute_event_hash(row.prev_hash,
                                row.tenant_id, row.agent_id,
                                row.action, row.tool,
                                row.decision, row.request_id)
assert recomputed == row.event_hash
```

### V3 — Per-shard prev_hash chain

Group rows by `chain_shard`; sort each shard by `(timestamp, id)`; for
each adjacent pair `cur.prev_hash == prev.event_hash`. The first row
in a shard MUST use `GENESIS_HASH` (or continue a prior chain — see §8).

### V4 — Merkle root signatures

For every entry in `bundle.merkle_roots`:

```
key = bundle.public_keys[kid := entry.kid]            # MUST exist
pk  = load_pem_public_key(key.pem.encode("utf-8"))
sig = base64url_decode_no_padding(entry.signature_b64)
pk.verify(sig, entry.signed_payload_canonical_json.encode("utf-8"))
```

`kid not in bundle.public_keys` is a V4 FAIL with reason
`unknown_kid`. The verifier MUST NOT fall back to any other key.

### V5 — Cross-day prev_root_hash chain

Sort `bundle.merkle_roots` by `root_date ASC`. For each adjacent pair:

```
assert cur.prev_root_hash in (prev.root_hash, None)
```

A `None` mid-chain is a SPEC violation — see §11.3.

### V6 — Retention metadata consistency

If `retention_metadata.earliest_row_in_bundle` is older than
`retention_metadata.configured_retention_days` days, the bundle FAILS
V6. A missing or unparseable retention block is a WARNING, not a FAIL.

---

## 16. Test vectors

This section is normative. A conformant verifier MUST produce the
exact result shown for each vector. (Vectors are in
`tools/aegis_verify/tests/test_verifier.py` for executable form.)

### 16.1 Minimal healthy bundle

```jsonc
{
  "format_version": "aegis-evidence-bundle/2026-06",
  "framework":      "eu-ai-act",
  "tenant_id":      "00000000-0000-0000-0000-000000000001",
  "period":         { "start": "2026-06-12", "end": "2026-06-13" },
  "generated_at":   "2026-06-13T23:00:00+00:00",
  "public_keys":    [ /* one ed25519 key */ ],
  "merkle_roots":   [ /* one root, signed */ ],
  "records":        [ /* three rows, chained */ ],
  "retention_metadata": {
    "policy":                     "6_months_minimum",
    "configured_retention_days":  180,
    "earliest_row_in_bundle":     "<row[0].timestamp>",
    "latest_row_in_bundle":       "<row[-1].timestamp>"
  }
}
```

**Expected:** `PASS` (V1-V6 all pass).

### 16.2 Tampered row

Edit `records[1].audit_row.decision` from `"allow"` to `"deny"` after
the chain is built; do NOT recompute the event_hash.

**Expected:** `FAIL` on V2 (`event_hash_recompute`), with
`first_broken_row_id == records[1].audit_row.id`.

### 16.3 Deleted row

Remove `records[1]` entirely from a 3-row chain.

**Expected:** `FAIL` on V3 (`prev_hash_chain_per_shard`) because the
remaining record at index 1's `prev_hash` no longer points to its
sibling's `event_hash`.

### 16.4 Forged signature

Replace `merkle_roots[0].signature_b64` with `base64(0x00 * 64)`.

**Expected:** `FAIL` on V4 (`merkle_root_signatures`).

### 16.5 Unknown key id

Set `merkle_roots[0].kid = "no-such-key-fingerprint"`.

**Expected:** `FAIL` on V4 with reason `unknown_kid`. Verifier MUST NOT
attempt fallback to another key.

### 16.6 Broken root chain

In a 3-day bundle, set `merkle_roots[2].prev_root_hash = "0" * 64`.

**Expected:** `FAIL` on V5.

### 16.7 Dishonest retention

`configured_retention_days = 30` but `earliest_row_in_bundle =
2020-01-01`.

**Expected:** `FAIL` on V6.

---

## 17. Versioning + forward compatibility

The `format_version` string in the bundle envelope is the version of
the *bundle format*. The version of *this spec* is `aevf/0.1.0`.

Future versions MAY:

- Add new top-level fields (verifiers MUST ignore unknown fields)
- Add new optional record fields
- Add new verification checks behind a NEW `format_version`

Future versions MUST NOT:

- Change `event_hash` recipe (would invalidate every historical row)
- Change canonical JSON forms (would invalidate every historical signature)
- Remove V1–V6

If V1 fails, the verifier MUST refuse to run V2–V6 (the bundle is not
AEVF-conformant and downstream results are meaningless).

---

## 18. Security considerations

### 18.1 What AEVF proves

A passing verification PROVES, with the security of SHA-256 +
Ed25519:

1. The audit rows in the bundle have not been individually altered
   since they were signed.
2. No row has been silently deleted from the middle of a shard.
3. The daily roots were signed by the named key.
4. The daily roots form an unbroken chain across the period.
5. The producer's retention claim is internally consistent.

### 18.2 What AEVF does NOT prove

- **Correctness of the decision.** AEVF proves what was recorded;
  whether the policy that produced the decision was correct is a
  separate question.
- **Completeness of the bundle.** A producer who signs only a
  subset of their audit rows can produce a passing bundle. AEVF
  alone cannot detect this; cross-vendor reconciliation (e.g. SIEM
  comparison, see the GRC adapter in §[future-section]) is the
  defense.
- **Legal admissibility.** A passing bundle is *evidence mapped to*
  EU AI Act / SOC 2 / NIST / DPDP requirements. Whether a court or
  audit firm accepts it is jurisdiction- and methodology-specific.
  AEVF intentionally takes no position.

### 18.3 Key compromise

If a signing key is compromised, an attacker can forge new Merkle
root signatures. The defense is:

- **Daily root publication off the producer's infrastructure** (e.g.
  archived to a customer's cold storage at end-of-day) so that a
  post-compromise rewrite cannot retroactively rewrite history the
  customer already holds.
- **Key rotation** with all historical keys preserved in
  `bundle.public_keys` so historical roots continue to verify.

### 18.4 Network isolation

A conformant verifier MUST run with networking disabled. The official
reference implementation (`tools/aegis_verify/`) has a networking-
disabled test (`tests/test_no_network.py`) that runs the verifier with
`socket.socket` patched to raise `PermissionError`. Independent
implementations are RECOMMENDED to ship a similar test.

---

## Appendix A. Reference implementation

The reference implementation is `tools/aegis_verify/` in the Aegis
repository. It is Apache 2.0 licensed and depends only on the Python
standard library plus `cryptography` (for ed25519). It implements V1
through V6 exactly as specified above. A user who is not running Aegis
can still run it:

```bash
pip install aegis-aevf
aegis-verify --bundle path/to/bundle.json
```

It is recommended (but NOT REQUIRED) that conformant independent
implementations match the reference implementation's CLI surface
(`--bundle`, `--verbose`, `--json`, `--print-spec-version`) so an
auditor's runbook does not change with the verifier they happen to use.

## Appendix B. Spec change log

| Version | Date | Change |
|---|---|---|
| `aevf/0.1.0` | 2026-06-14 | Initial public draft. |
