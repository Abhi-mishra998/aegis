# Service Mesh Authentication

*Per-service ES256 mesh JWTs replace the shared `INTERNAL_SECRET` so a leak of one service's private key cannot forge another service's tokens.*

## What changed

Pre-Sprint-1, every Aegis service signed and verified mesh tokens with the
same HS256 secret (`MESH_JWT_SECRET`, with a fallback to `INTERNAL_SECRET`).
The audit (C12) called this out as the literal opposite of the README claim
"no single shared secret" — leaking one service's key forged every other
service's tokens.

Sprint 1.4 introduces per-service ES256 (ECDSA P-256) keypairs:

- Each service owns one **private** key it uses to sign mesh tokens.
- Verifying services hold the **public** keys of every service whose tokens
  they accept (a trust registry, keyed by service name).
- The JOSE `kid` header carries the issuer service name so the verifier
  picks the right public key on every request.
- Leaking service A's private key cannot mint service B's tokens — the
  verifier's `kid` lookup binds the signature to a single trusted public key.

Legacy HS256 + `INTERNAL_SECRET` lanes remain for back-compat **only while
asymmetric keys are not yet configured**. The moment `ACP_MESH_TRUSTED_KEYS`
is set, `verify_internal_secret` rejects the legacy `X-Internal-Secret`
header with HTTP 403. This closes the "an old leaked secret keeps working"
seam.

## Configuration

Source: `sdk/common/auth.py`.

Each service process needs three env vars to participate fully:

| Variable | Purpose | Required for |
|---|---|---|
| `ACP_MESH_SERVICE_NAME` | This process's identity (e.g. `gateway`, `audit`). Stamped into `iss` and `kid` on minted tokens. | minting only |
| `ACP_MESH_PRIVATE_KEY_PEM` | This service's ES256 private key, base64-encoded PKCS8 PEM. | minting only |
| `ACP_MESH_TRUSTED_KEYS` | JSON `{service_name: base64-PEM}` registry of every public key this process accepts. | verifying only |

Generate a keypair (one per service):

```bash
openssl ecparam -name prime256v1 -genkey -noout -out gateway.priv.pem
openssl ec -in gateway.priv.pem -pubout -out gateway.pub.pem
base64 -i gateway.priv.pem            # → ACP_MESH_PRIVATE_KEY_PEM for gateway
base64 -i gateway.pub.pem             # → entry in every verifier's trust registry
```

Compose example for the gateway service:

```yaml
gateway:
  environment:
    ACP_MESH_SERVICE_NAME: gateway
    ACP_MESH_PRIVATE_KEY_PEM: ${GATEWAY_MESH_PRIVATE_KEY_PEM_B64}
    ACP_MESH_TRUSTED_KEYS: |
      {
        "gateway": "${GATEWAY_MESH_PUB_B64}",
        "audit":   "${AUDIT_MESH_PUB_B64}",
        "policy":  "${POLICY_MESH_PUB_B64}"
      }
```

Audit-side: same shape, but the audit service only verifies tokens minted
by clients (gateway, decision, etc.) — it does not mint mesh tokens itself
unless it calls another service. Set only `ACP_MESH_TRUSTED_KEYS` if so.

## How the verifier picks a key

`_verify_mesh_jwt(token)` inspects the JOSE header:

1. If `alg == ES256`: look up `kid` in `ACP_MESH_TRUSTED_KEYS`. Verify the
   signature against that public key. Unknown `kid`, missing trust entry, or
   signature failure → reject. **No fallback to HS256** — the asymmetric
   path is strict.
2. Otherwise (header `alg` is HS256 or absent): verify against the legacy
   `MESH_JWT_SECRET` / `INTERNAL_SECRET`. This lane is the back-compat path
   for un-rotated deployments.

The legacy `X-Internal-Secret` header is accepted only when no asymmetric
mesh keys are configured. Once `ACP_MESH_TRUSTED_KEYS` is set in any
verifying process, the header lane is closed there.

## Migration

Recommended rollout for an existing deployment:

1. Generate per-service keypairs offline. Distribute private keys via the
   same channel as other production secrets (SSM SecureString, Secrets
   Manager, sealed-secrets — never plaintext in a compose file).
2. Build a shared trust registry JSON: `{name: pub_pem_b64}` for every
   service. The same JSON ships to every verifier as `ACP_MESH_TRUSTED_KEYS`.
3. Roll out the keys to **verifiers first** with no minters configured.
   Verifiers still accept HS256 from un-rotated minters — no traffic break.
4. Roll out `ACP_MESH_PRIVATE_KEY_PEM` + `ACP_MESH_SERVICE_NAME` to
   minters. They start emitting ES256 tokens; verifiers route them via
   `kid` lookup. HS256 traffic still works.
5. Once every minter has been rotated, remove `INTERNAL_SECRET` from every
   service's env. The legacy lane is now hard-closed everywhere.

## Verification

Sprint 1.4 ships nine test cases proving the contract
(`tests/test_mesh_auth.py`):

- ES256 mint→verify round trip across service A and service B.
- Service A's leaked key cannot forge a service-B token (the C12 fix).
- Unknown `kid` is rejected.
- A `kid` outside the trust registry is rejected.
- Legacy HS256 still works when no mesh keys are configured.
- `verify_internal_secret` accepts the legacy header only when mesh keys
  are absent; rejects it once they are configured.
- A valid ES256 mesh JWT is accepted on the `X-Mesh-Token` header.

## Operational checklist

- [ ] Generate one ES256 keypair per service.
- [ ] Store private keys in SSM Parameter Store (SecureString).
- [ ] Distribute the trust registry to every verifier.
- [ ] Verify no service in the stack still ships `INTERNAL_SECRET` after
      rotation completes.
- [ ] Document the rotation cadence in [Key Rotation](../operations/key-rotation.md).

## Next

- [Crypto Audit Chain](crypto-audit-chain.md) — how the audit log's signing
  keys are managed independently of mesh keys.
- [Key Rotation](../operations/key-rotation.md) — operator runbook for
  rotating any of the keys in this document.
- [Secret Management](secret-management.md) — where secrets live and who
  has access.
