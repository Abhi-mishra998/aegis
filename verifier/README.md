# ACP Audit Log Verifier

A standalone, open-source tool for verifying ACP audit trails — no ACP
server, no trust in ACP's infrastructure required.  Given a downloaded export
bundle you can prove, using only standard cryptography, that your audit trail
was not tampered with after the fact.

## What it verifies

Three properties are checked, all offline:

1. **Receipt integrity** — every execution receipt carries a valid
   [ed25519](https://ed25519.cr.yp.to/) signature produced by the ACP signing
   key.  A single bit flip in the payload body invalidates the signature.

2. **Merkle inclusion** — every receipt was committed into a daily Merkle tree
   before the tree root was signed and published.  The inclusion proof lets you
   re-derive the root from your leaf; if it matches the signed root, the
   receipt was genuinely in the tree at sealing time.

3. **Root-chain consistency** — daily roots form an append-only linked list:
   each root commits to the hash of the previous day's root (`prev_root_hash`).
   An attacker who rewrites an old root would break this chain, which any
   customer who archived an earlier root would immediately detect.

## Install

```bash
# Standalone verifier only (minimal deps: cryptography + pyyaml)
pip install acp-verify

# Or via the full ACP SDK
pip install "acp[verify]"
```

## Get an export from your ACP deployment

```bash
acp archive \
    --base-url https://acp.example.com \
    --token "$ACP_API_KEY" \
    --out ./my-export \
    --since 2026-05-01T00:00:00Z \
    --until 2026-05-31T23:59:59Z
```

This writes:

```
my-export/
  keys/active.pem
  receipts/{execution_id}.json
  proofs/{execution_id}.json
  roots/{YYYY-MM-DD}.json
```

## CLI examples

**Verify a whole export bundle (most common):**

```bash
acp verify export ./my-export
```

**Verify one receipt against a public key:**

```bash
acp verify receipt ./my-export/receipts/abc123.json \
    --pubkey ./my-export/keys/active.pem
```

**Verify the root chain only:**

```bash
acp verify chain ./my-export/roots/
```

**Verify a single Merkle inclusion proof:**

```bash
acp verify inclusion \
    ./my-export/receipts/abc123.json \
    ./my-export/proofs/abc123.json
```

All commands exit `0` on success, `1` on any failure.  Add `--json` for
machine-readable output suitable for CI pipelines or SIEM ingestion.

## How the cryptography works

**Receipt signatures** use [ed25519](https://ed25519.cr.yp.to/), a modern
elliptic-curve signature scheme from the `cryptography` library.  When ACP
records an execution, it serialises the receipt fields as canonical JSON
(keys sorted, no extra whitespace, UTF-8) and signs the resulting bytes with
the platform's ed25519 private key.  The verifier loads the public key you
downloaded once from the deployment, re-serialises the receipt the same way,
and calls `Ed25519PublicKey.verify(signature, message)`.  The `cryptography`
library raises `InvalidSignature` if even one byte differs; the verifier maps
this to `ok=False`.  Key fingerprints (SHA-256 of the PEM, first 32 hex
chars) let the verifier match a receipt to the right key after rotation
without comparing full PEM blobs.

**Merkle trees** are built bottom-up from SHA-256 leaf hashes.  Each leaf is
`sha256(canonical_json(signed_receipt_payload))` — the entire payload
including the signature field, so the tree commits to both the content and the
authenticity of every entry.  Inner nodes are
`sha256(left_child_bytes + right_child_bytes)` where the child bytes are the
raw 32 bytes decoded from the hex hashes; odd-count levels duplicate the last
node (Bitcoin-style) so every level has an even count.  To verify inclusion
you start with the leaf hash and walk the sibling list: for a left sibling
compute `sha256(sibling + current)`, for a right sibling compute
`sha256(current + sibling)`.  The result after all siblings must equal the
signed root hash — if it does, the receipt was provably in the tree when the
root was sealed.

## Python API

```python
from pathlib import Path
from acp_client.verifier import AuditVerifier

# Load keys from the export bundle
verifier = AuditVerifier.from_export_dir(Path("my-export"))

# Verify everything at once
result = verifier.verify_export(Path("my-export"))
print(f"receipts: {result.valid_receipts}/{result.total_receipts}")
print(f"proofs:   {result.valid_inclusions}/{result.total_inclusions}")
print(f"chain ok: {result.chain_ok}")
assert result.ok
```
