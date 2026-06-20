# ADR-005: ed25519 over RSA-2048 for audit-chain signing

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: crypto, audit, transparency, signing, key-management

## Context

Aegis signs daily per-tenant Merkle roots of the audit log (see
ADR-001). The signature is the linchpin of the "tamper-evident" claim
— it lets any third party with our public key and the published root
prove that the audit chain hasn't been rewritten.

Every choice in this signing pipeline carries weight:

- **Verifier convenience.** Customers run `aegis-verify` to walk the
  chain from any historical root. The PyPI package must be small,
  install fast, and depend on only one mainstream crypto library.
- **Signature size.** Every audit-log entry that customers may want to
  bundle into a portable AEVF evidence pack also carries a signature.
  At scale (10k receipts in a bundle), a 256-byte RSA-2048 signature
  multiplies into a 2.5 MB overhead; a 64-byte ed25519 signature is
  640 KB. The bundle is a customer download, not a server-side
  artefact — size matters for UX.
- **Public-key size.** Every customer who archives a root archives the
  public key with it. 32 bytes vs 256 bytes is small absolutely but
  it's the smaller default and matters for the "fits in a tweet"
  story.
- **Sign / verify speed.** Daily root + thousands of receipts per day.
  ed25519 verify is ~10× faster than RSA-2048 verify at our scale.
- **Industry signal.** What does the Sigstore / SSH-CA / TUF
  ecosystem use? We want to be in the mainstream so a CISO sees
  "ed25519" and thinks "yes, that's the modern default."
- **Quantum future.** Neither ed25519 nor RSA-2048 survive a CRQC
  (cryptographically relevant quantum computer). We accept this
  the same way the rest of the industry does and revisit when
  NIST PQC migration becomes pragmatic (2030+).

## Decision

Every audit-chain Merkle root is signed with **ed25519**. Keys are
generated and stored per-tenant inside AWS Secrets Manager + KMS CMK
envelope (one signing key per tenant per region). The wire format is
the AEVF spec at `docs/AEVF/spec.md`; the public CLI is
`pip install aegis-aevf` (source at `tools/aegis_verify/`).

The signing library is `cryptography.hazmat.primitives.asymmetric.
ed25519` (`services/audit/signer.py:49-50`). The verifier uses
the same library (`tools/aegis_verify/verifier.py` declares
"cryptography" as its single runtime dep at line 100).

The historical-keys table
(`services/audit/alembic/versions/h3i4j5k6l7m8_*.py`) stores every
previous signing key alongside the active one so a receipt signed by
a rotated key is still verifiable months later.

## Alternatives considered

1. **RSA-2048 PKCS#1 v1.5.** Most-familiar to enterprise CISOs;
   universal HSM support. Rejected because:
    - 8× larger signatures (256 vs 64 bytes) — meaningful at scale
      for portable evidence bundles.
    - ~10× slower verify — meaningful for the verifier-CLI UX when
      customers walk a year of daily roots.
    - 2026 best-practice has moved away from RSA-2048 (NIST SP
      800-131A "disallowed after 2030" for 2048-bit RSA).
    - The "RSA is what enterprises know" objection is dissolving;
      every modern Sigstore / Fulcio / SSH-CA chain uses
      Ed25519/ECDSA.
2. **ECDSA P-256.** What Sigstore Fulcio uses internally. Reasonable
   alternative. Rejected (slightly) for:
    - Signature size is comparable (~64 bytes) but ECDSA has the
      historical "nonce-reuse leaks the private key" footgun that
      ed25519's deterministic design avoids.
    - ed25519's `cryptography` API surface is simpler — fewer ways to
      mis-use it.
   We DO accept ECDSA P-256 in the cosign bundle-signing path (see
   ADR-NNN release-bundle-cosign — to be written) because that's the
   Sigstore default and we don't want to fork the ecosystem.
3. **HSM-backed RSA-3072.** Maximum-paranoia option. Rejected because
    we don't yet need an HSM (AWS Secrets Manager + KMS envelope
    encryption + per-tenant CMK is already in the "tier 3 retention"
    class per `docs/security/data_classification.md`), and the
    operational complexity of an HSM (CloudHSM cluster + per-region
    HA + per-tenant key onboarding) is a year of work we don't have
    a customer asking for.
4. **HMAC-SHA256.** Symmetric. Rejected — symmetric signatures can't
   be verified by a third party who doesn't already share the secret.
   Defeats the public-transparency story.

## Consequences

* **Positive**
  - 64-byte signatures × thousands of receipts per evidence bundle ≈
    small download.
  - Single `cryptography` dep in the verifier — fast `pip install`,
    no transitive churn.
  - Mainstream choice — CISO sees "ed25519" and moves on.
  - Per-tenant CMK isolation lets a customer revoke our access to
    THEIR key without affecting other tenants — a property regulated
    buyers ask for explicitly.
* **Negative**
  - No mainstream HSM has first-class ed25519 support for the API
    we'd want. If a customer's contract requires HSM-backed signing,
    we'd have to either (a) negotiate "AWS KMS envelope is HSM-backed
    at the AWS layer" or (b) add a separate ECDSA-on-HSM signing
    path. Re-evaluate when this lands as a customer request.
  - Some legacy auditors are RSA-only by training; spend a 5-minute
    explanation on "why ed25519 is now best-practice" in sales
    calls. Cite NIST SP 800-186 + Sigstore.
* **Reversibility**
  - **Possible but expensive.** Adding RSA signatures alongside
    ed25519 is mechanical (the
    `services/audit/historical_keys` table already separates the
    storage of any rotation); rotating to RSA-only would deprecate
    all previously-published proofs.

## Implementation references

* `services/audit/signer.py:49-50` — `cryptography.hazmat.primitives.
  asymmetric.ed25519` import
* `services/audit/transparency.py:434-435` — same import for the
  per-root signing path
* `services/audit/transparency.py:519,529` — runtime assertions that
  the public key is the right type + that the signature is the
  required 64 bytes
* `services/audit/alembic/versions/h3i4j5k6l7m8_add_transparency_columns_and_historical_keys.py` —
  rotation-friendly historical keys table
* `tools/aegis_verify/verifier.py:17,36,100,313` — verifier reads
  ed25519 from the bundle metadata + asserts at verify time
* `docs/AEVF/spec.md` — wire format
* `docs/runbooks/secrets_rotation.md` — per-tenant key rotation playbook

## Verification

```bash
# 1. Confirm the signer is using the right algorithm at runtime.
docker exec acp_audit python3 -c \
  "from services.audit.signer import get_signer; print(type(get_signer()).__name__)"
# expect: Ed25519Signer (or similar)

# 2. Pull a real root, inspect the algorithm field.
aws s3 cp s3://aegis-public-roots-628478946931/roots/<tenant>/$(date -u -d 'yesterday' +%Y-%m-%d).json - \
  --no-sign-request | jq '.signing_keys[0].algorithm'
# expect: "ed25519"

# 3. Verify with the published CLI — full chain walk + signature check.
pip install aegis-aevf
aegis-verify --bucket aegis-public-roots-628478946931 --tenant <tenant>
# expect: V1-V6 PASS (V3 is the ed25519 signature check)
```
