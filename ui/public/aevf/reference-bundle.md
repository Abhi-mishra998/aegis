# Reference Evidence Package

> A real, deterministic, signed AEVF bundle. Any auditor can download it, run [`aegis-verify`](https://pypi.org/project/aegis-aevf/), and see **PASS** in 60 seconds.

## What it is

A self-contained example AEVF bundle conforming to `aegis-evidence-bundle/2026-06`, signed with a deterministic ed25519 keypair, covering 5 audit records over 2 days. Designed to make the [specification](./spec.md), the [auditor checklist](./auditor-checklist.md), and the [reference audit report](./reference-audit-report.md) concrete.

## The bundle

| Attribute | Value |
|---|---|
| Download | [`reference-bundle-2026-06.json`](https://aegisagent.in/aevf/reference-bundle-2026-06.json) |
| Size | 9 165 bytes |
| SHA-256 | `8a6f09f65c374edf44c811dba8f146c8d79dab9ed74e3c49920be759951f20fc` |
| Format | `aegis-evidence-bundle/2026-06` |
| Spec version | `aevf/0.1.0` |
| Tenant | `11111111-1111-1111-1111-111111111111` |
| Period | 2026-06-12 → 2026-06-13 |
| Records | 5 (3 allow, 2 deny, ... see breakdown below) |
| Public keys | 1 ed25519 (deterministic from a published seed) |
| Merkle roots | 2 daily roots, chained |

## What the records evidence

| # | Day | Tool | Decision | Reason | Showcases |
|---|---|---|---|---|---|
| 0 | 2026-06-12 | `tool.read_file` | allow | — | Mundane baseline (chain start) |
| 1 | 2026-06-12 | `tool.sql_query` | **deny** | `bulk_pii_egress_above_threshold` | Fintech bulk-PII export blocked on a `medium`-risk agent |
| 2 | 2026-06-12 | `tool.sql_query` | allow | — | Benign analytics read (proves chain spans allow + deny) |
| 3 | 2026-06-13 | `tool.shell` | **deny** | `k8s_prod_namespace_destruction` | `kubectl delete ns prod` blocked on a `low`-risk agent |
| 4 | 2026-06-13 | `tool.http_request` | **escalate** | `external_pii_exfil` | External PII email routed to human approval queue |

Every record carries explicit mappings to **EU AI Act articles**, **SOC 2 controls**, **NIST AI RMF functions**, and **India DPDP sections**.

## 60-second verification

```bash
# Download
curl -O https://aegisagent.in/aevf/reference-bundle-2026-06.json

# Confirm bytes match
sha256sum reference-bundle-2026-06.json
# → 8a6f09f65c374edf44c811dba8f146c8d79dab9ed74e3c49920be759951f20fc

# Install verifier
pip install aegis-aevf

# Verify offline (disconnect Wi-Fi first if you want strict isolation)
aegis-verify --bundle reference-bundle-2026-06.json
# → [PASS] V1 through V6
# → *** PASS *** every signature, hash chain, and Merkle root verifies.
```

## Tamper-detection demo

Prove the verifier catches a single-byte change:

```bash
python3 -c "
import json
d = json.load(open('reference-bundle-2026-06.json'))
d['records'][1]['audit_row']['decision'] = 'allow'   # was 'deny'
json.dump(d, open('tampered.json', 'w'))
"
aegis-verify --bundle tampered.json
# → [FAIL] V2_event_hash_recompute
# → first broken row: aaaaaaaa-0000-4000-8000-000000000002
```

The verifier names the exact broken row.

## Determinism

The bundle is byte-deterministic. The script that produced it (`scripts/aevf/build_reference_bundle.py`) seeds the ed25519 keypair from `sha256("AEVF_REFERENCE_BUNDLE_SEED_2026_06")`. Anyone running the script on a host with the same `cryptography` library version will produce the **exact same bytes** — including the same SHA-256.

```bash
# Same hash on two independent runs:
python3 scripts/aevf/build_reference_bundle.py --out /tmp/b1.json | grep sha256
python3 scripts/aevf/build_reference_bundle.py --out /tmp/b2.json | grep sha256
# → both: 8a6f09f65c374edf44c811dba8f146c8d79dab9ed74e3c49920be759951f20fc

diff -q /tmp/b1.json /tmp/b2.json
# → (no output — identical)
```

Determinism is the moat-amplifier. An auditor who suspects the bundle they downloaded has been altered can regenerate from the script and compare.

## Why this exists

[`spec.md`](./spec.md) defines the format. [`auditor-checklist.md`](./auditor-checklist.md) defines the procedure. [`reference-audit-report.md`](./reference-audit-report.md) defines the deliverable. Without a real concrete bundle, all three reference a hypothetical artifact — useful in theory, untested in practice.

This bundle makes the three documents executable. A practitioner can step through every line of the Auditor Checklist against a real, signed, downloadable file and form a real opinion about whether they would accept evidence produced under AEVF in an engagement.

That opinion — yes or no — is the **Auditor Gate** from `final-sprint.md` v3. It is now testable without a sales call.

## See also

- [Specification](./spec.md) — byte-precise AEVF v0.1.0
- [Auditor Checklist](./auditor-checklist.md) — 25-minute procedure
- [Reference Audit Report](./reference-audit-report.md) — engagement-ready template
- [`aegis-verify` on PyPI](https://pypi.org/project/aegis-aevf/) — reference implementation
