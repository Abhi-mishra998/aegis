# aegis-verify

> Offline verifier for Aegis evidence bundles. **Zero network calls.**
> One pip dep (`cryptography`). Runs on an air-gapped machine.
>
> Built for an EU AI Act auditor who wants to verify what your AI agents
> did — or were stopped from doing — without trusting Aegis or being
> connected to the internet.

## Install

```bash
pip install cryptography           # the only runtime dependency
# or, from this repo:
pip install -e tools/aegis_verify
```

## Use

```bash
# verifier reads the bundle file and prints a report
python -m aegis_verify --bundle evidence_bundle.json

# fail any check → exit 1; pass all → exit 0 (CI-friendly)
python -m aegis_verify --bundle evidence_bundle.json && echo OK
```

Output on a healthy bundle:

```
aegis-verify report
  bundle:     aegis-evidence-bundle/2026-06
  framework:  eu-ai-act
  tenant:     00000000-0000-0000-0000-000000000001
  records:    482
  keys:       1
  roots:      6

Checks:

*** PASS *** every signature, hash chain, and Merkle root in this bundle verifies.
```

Output on a tampered bundle:

```
Checks:
  [FAIL] V3_prev_hash_chain_per_shard — 1 prev_hash mismatch(es) (first: <uuid>)
*** FAIL *** at least one check failed.
             first broken row: <uuid>
```

## What it verifies

| Check | What it proves |
|---|---|
| `V1_bundle_format_recognized` | The bundle is a format the verifier understands |
| `V2_event_hash_recompute` | Each row's `event_hash` recomputes from its content — no row was edited after the fact |
| `V3_prev_hash_chain_per_shard` | The intra-day hash chain has zero breaks — no row was deleted |
| `V4_merkle_root_signatures` | Each daily Merkle root was ed25519-signed by Aegis (the embedded public key proves it) |
| `V5_prev_root_hash_chain` | The cross-day Merkle-root chain has zero breaks — no day was excised |
| `V6_retention_metadata_consistent` | The bundle's claimed retention policy is honest given the rows actually present |

## Why this matters

The EU AI Act high-risk obligations reach full enforcement on
**August 2, 2026** (Article 12: tamper-evident record-keeping ≥ 6
months; Article 14: human oversight events). Penalties: 7% global
turnover / €35M.

Every other AI-guardrail vendor's evidence story ends with *"trust our
dashboard."* That doesn't survive an external auditor.

This verifier ends with: *"the auditor ran a 16 KB Python script on
their own laptop and got PASS. No trust required."*

## Bundle format

See `verifier.py`'s module docstring for the full schema. In short:
the bundle is one self-contained JSON file containing the public keys,
the signed daily Merkle roots, every audit row in scope, and a per-row
mapping to EU AI Act articles / NIST AI RMF controls / SOC 2 control IDs.

## License

Apache 2.0. The verifier is and will remain open-source so auditors
can fork it and run their own copy.
