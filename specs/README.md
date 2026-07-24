# ATF v3.2 — Public Formats

This directory carries the JSON Schema definitions for every ATF artifact a
third party is expected to consume:

| File | Serves | Reference in `ATF_v3.0_Verifiable_Kernel.md` |
|---|---|---|
| `ledger_entry.schema.json`     | Verifier / SIEM / auditor ingesting one ledger entry              | §7.1 + §7.4 |
| `witness_attestation.schema.json` | Verifier / SOC tooling checking a Witness verdict signature      | §6.4 |
| `export_bundle.schema.json`    | Regulator / auditor consuming a full period export                | §7.3 + §7.4 |

## Semver rules (§7.4)

- Verifiers accept every MINOR under the same MAJOR.
- Unknown MAJOR → refuse (`UnsupportedBundleVersion`).
- Hash and signature verification is schema-agnostic; canonical-form hashing
  (RFC 8785 JCS) covers all fields whether or not the verifier semantically
  understands them.
- Every artifact self-declares its schema version.

## Canonicalization

All signing and hashing operates on RFC 8785 JSON Canonicalization Scheme
serializations. See `sdk/common/jcs_check.py` — the Python reference
implementation exposes vectors so a second implementation can prove
byte-identity on the §7.1 domain (I6 in the Security Invariants).

## Not a spec repository

These are **published contracts**, not a spec fork. The reference Python
implementation lives under `sdk/common/` and `services/witness/`. Third
parties are expected to:

1. Read the schemas here.
2. Implement their own verifier / consumer in the language of their choice.
3. Cross-check against `aegis-verify` on the fixture corpus.

## Roadmap-4 items (§Phase 4)

- IETF AIMS / NCCoE alignment: pending the standards' next milestone.
- Consortium anchoring: gated at ≥ 10 tenants demanding it.
