# AEVF — Aegis Evidence Verification Format

> **The open standard for AI-decision evidence that an auditor can verify without trusting you.**

[![Spec version](https://img.shields.io/badge/aevf-0.1.0-blue)]()
[![Bundle format](https://img.shields.io/badge/bundle-2026--06-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-green)]()

## What this is

AEVF is the bundle format + verification algorithm that lets you give an auditor a `.json` file and have them prove, with their own tools, that **the AI-decision records inside have not been altered since they were signed.** No vendor account. No API key. No network call.

The complete specification is in **[spec.md](./spec.md)**. It is precise enough that an engineer with SHA-256, Ed25519, and base64 can write an independent verifier from the document alone.

## Why this exists

The buying question for high-risk AI in 2026 is:

> *"If the vendor that made the decision disappears tomorrow, would your auditor still be able to verify your evidence?"*

A logging vendor cannot answer "yes" to that question. A monitoring tool cannot answer "yes". AEVF lets you answer "yes" — by spec, by code, and by handing the auditor the same verifier the vendor uses.

## The 60-second auditor flow

```bash
# 1. Auditor receives bundle.json from the customer.
$ aegis-verify --bundle bundle.json

aegis-verify report
  bundle:     aegis-evidence-bundle/2026-06
  framework:  eu-ai-act
  tenant:     00000000-0000-0000-0000-000000000001
  records:    1402
  keys:       1
  roots:      30

  [PASS] V1_bundle_format_recognized
  [PASS] V2_event_hash_recompute        — 1402 rows pass
  [PASS] V3_prev_hash_chain_per_shard   — 16 shards, 0 breaks
  [PASS] V4_merkle_root_signatures      — 30 roots verified
  [PASS] V5_prev_root_hash_chain        — chain intact
  [PASS] V6_retention_metadata_consistent

*** PASS *** every signature, hash chain, and Merkle root verifies.
```

If a row was altered:

```bash
  [PASS] V1_bundle_format_recognized
  [FAIL] V2_event_hash_recompute        — 1 row(s) have event_hash
                                          that doesn't recompute from
                                          content (first: 7c8a9e3f-...)
  ...

*** FAIL *** at least one check failed.
             first broken row: 7c8a9e3f-3215-4f5a-9a31-d04f2a5b7c01
```

## What's in an AEVF bundle

```jsonc
{
  "format_version": "aegis-evidence-bundle/2026-06",
  "framework":      "eu-ai-act",
  "tenant_id":      "...",
  "period":         { "start": "...", "end": "..." },

  // Every ed25519 public key that signed anything below.
  "public_keys": [ { "kid": "...", "pem": "...", "algorithm": "ed25519" } ],

  // One signed Merkle root per day in the period. Roots chain across days.
  "merkle_roots": [
    { "root_date": "2026-06-13", "root_hash": "...", "signature_b64": "...",
      "kid": "...", "prev_root_hash": "..." }
  ],

  // Per-decision records. Each row is hash-chained to its predecessor.
  "records": [
    {
      "audit_row": { "id": "...", "decision": "deny", "event_hash": "...",
                     "prev_hash": "...", "chain_shard": 0, ... },
      "mappings":  { "eu_ai_act": ["Article 12"] },
      "merkle_root_date": "2026-06-13"
    }
  ],

  "retention_metadata": { "policy": "6_months_minimum", ... }
}
```

## The six verification checks

| Check | What it proves |
|---|---|
| **V1** Bundle format recognized | The verifier knows how to read this file. |
| **V2** Per-row event_hash recompute | No row has been individually altered. |
| **V3** Per-shard prev_hash chain | No row has been silently deleted from the middle. |
| **V4** Merkle root signatures | The producer's signing key actually signed the roots. |
| **V5** Cross-day root chain | No day has been retroactively rewritten. |
| **V6** Retention metadata consistency | The producer's retention claim is honest. |

All six are defined byte-precisely in [spec.md §15](./spec.md#15-verification-algorithm-v1v6).

## Why "open standard, not just a tool"

If AEVF lives only inside one vendor's source code, it's not a standard — it's an implementation detail. An open spec means:

1. **An auditor can build their own verifier** and not trust ours. (We *want* them to.)
2. **A second-source verifier can exist.** One will, written by an audit firm. The spec makes that possible.
3. **The format outlives any one vendor.** If Aegis disappears tomorrow, evidence already produced still verifies — because the verifier is open, the spec is open, the bundle is self-describing.

This is the only structural answer to "can the auditor trust the evidence, or do they have to trust the vendor?" — they can trust the *math*, not us.

## License

This specification and the reference implementation are licensed under the **Apache License, Version 2.0** (see [LICENSE](../../LICENSE)). You can read it, implement it, fork the reference implementation, and ship a competing implementation — that's the design.

## Implementing your own verifier?

The spec is in [spec.md](./spec.md). The reference implementation is in [`tools/aegis_verify/`](../../tools/aegis_verify/) (Python). It depends only on the standard library plus `cryptography` (for ed25519) — no Aegis-specific imports, no network code.

If you find an ambiguity in the spec that two independent implementations could resolve differently, please file an issue. Ambiguity is a spec defect.

## See also

- **[spec.md](./spec.md)** — the byte-precise specification
- **[`tools/aegis_verify/`](../../tools/aegis_verify/)** — Python reference implementation
- **[`tools/aegis_verify/tests/test_verifier.py`](../../tools/aegis_verify/tests/test_verifier.py)** — executable test vectors
- **[Aegis live demo](https://aegisagent.in/live-demo)** — produces AEVF bundles end-to-end
