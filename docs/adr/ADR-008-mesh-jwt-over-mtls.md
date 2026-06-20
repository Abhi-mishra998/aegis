# ADR-008: ES256 mesh JWT over mTLS for service-to-service auth

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: auth, mesh, crypto, deploy, supply-chain

## Context

Aegis runs ~32 services on each EC2 host (per ADR-007). When the
gateway calls identity-svc, or identity-svc calls audit-svc, or audit-
svc calls policy-svc, we need every callee to prove three things about
the caller:

1. The request actually came from another Aegis service (not from a
   compromised customer container, not from a leaked credential
   replayed by an external attacker).
2. The caller's identity is specifically *which* service (so callee
   can enforce "only the gateway calls /internal/throttle").
3. The credential is short-lived enough that a leak has a bounded
   blast radius.

The 2026-06-17 security audit (per ADR-003 context) flagged the
pre-existing model — every service signed and verified with the SAME
`X-Internal-Secret` HS256 token — as the **largest standing security
debt** in the platform. A leak of one container's env vars compromised
every service in the mesh. The Sprint EH-5 design closed this with
per-service ES256 keypairs; this ADR is the durable record of why
ES256 mesh JWT, not the more-obvious mTLS, won the design.

## Decision

Inter-service authentication uses **ES256-signed JWTs**, one keypair
per service:

- Each service holds **one** ECDSA P-256 private key, loaded at boot
  from SSM at `/aegis-prodha/mesh/<service>/private` (SecureString +
  KMS-encrypted) via the `ACP_MESH_PRIVATE_KEY_PEM` env var.
- Each service holds the **public-key map of every service it accepts
  tokens from**, loaded at boot from the SSM trusted-keys document at
  `/aegis-prodha/mesh/trusted-keys` via the `ACP_MESH_TRUSTED_KEYS`
  env var.
- The caller signs a JWT with the audience `acp.mesh.internal` + the
  caller-service name in `sub`. Token TTL: 60 seconds.
- The callee verifies the audience + the signature against the right
  service's public key + the freshness window.
- During the migration window the legacy `X-Internal-Secret` fallback
  remains accepted; flipping `/aegis-prod/mesh_legacy_fallback=false`
  in SSM completes the cutover (OP-2 step).

Key minting + SSM upload is automated by
`scripts/ops/generate_mesh_keys.py`. Library code lives in
`sdk/common/auth.py:49-73`. Rotation cadence + procedure live in
`docs/runbooks/secrets_rotation.md` §1.

## Alternatives considered

1. **Shared `X-Internal-Secret`** (the previous model). Rejected —
   single-secret compromise = total mesh compromise. The audit C12
   finding was specifically this.
2. **mTLS via Smallstep CA.** The most-cited "right" answer in the
   industry. Rejected because:
    - Adds a CA we have to operate (Smallstep step-ca lifecycle,
      root rotation, intermediate signing, OCSP / CRL distribution).
    - Per-service cert rotation needs a sidecar (cert-manager-like)
      OR the operator handcrafts a cron that re-fetches certs from
      the CA. Both add a moving part on every host.
    - TLS handshake adds ~5-15 ms latency per request — meaningful
      because the gateway typically fans out to 4-6 services per
      `/execute` call.
    - HTTP-layer code changes are non-trivial: every service has to
      switch from `httpx.AsyncClient()` to `httpx.AsyncClient(verify=
      ca_bundle, cert=cert_pair)`. Multiplied across 32 services it's
      a sprint of yak-shaving.
   We may revisit when (a) we move to K8s (where istio-style sidecar
   mTLS becomes operationally cheap) OR (b) a customer contractually
   requires mTLS attestation.
3. **SPIFFE / SPIRE.** The Cloud-Native-y "right" answer. Rejected
   for the same operability reason as mTLS — SPIRE is itself a
   distributed system (SPIRE server + agents + a workload-attestation
   plugin per pod). At our scale we're carrying the operator burden
   for zero customer-visible benefit.
4. **HS256 mesh JWT** (per-service HMAC secrets, shared between
   pairs). Rejected — symmetric pair-wise keys multiply badly
   (32 × 32 = 1024 pairs at full mesh; even a star topology
   needs 32 secrets). And we still don't get the leaked-key-revocation
   property that asymmetric gives us.
5. **OAuth 2.0 client-credentials flow with a separate IdP.** Adds
   a network round-trip + an IdP we have to operate. Rejected for
   the same operability reason as SPIRE.

## Consequences

* **Positive**
  - Leak of one service's env vars = leak of *that one service's*
    private key only. The other 31 services keep authenticating
    correctly; revoking the leaked service is one SSM update + an
    ASG instance refresh.
  - 60-second token TTL bounds the replay window.
  - ECDSA P-256 is a JOSE standard — every JWT library
    (`pyjwt`, `python-jose`, even `node-jose`) handles ES256 natively.
    No proprietary signature format.
  - The same key-discovery path (`/aegis-prodha/mesh/<service>/`)
    extends naturally to a per-region split for the EU instance
    (Sprint EI-5).
* **Negative**
  - Key rotation is per-service, not blanket — operator must walk all
    32 services to rotate them all. Mitigated by the `generate_mesh_
    keys.py` helper that handles the SSM upload in one shot; what's
    NOT mitigated is the ASG-roll cost (services rotate keys when
    they restart). Quarterly rotation cadence is realistic.
  - JWT bearers in headers can leak via logs that bind too eagerly to
    request metadata. Mitigated by `pyjwt`'s lifetime-bounded tokens
    and structlog scrubbing of `Authorization` headers (in
    `sdk/common/log_scrubber.py`).
  - The `MESH_LEGACY_FALLBACK=true` window is the period of greatest
    operational risk — both the old `X-Internal-Secret` AND the new
    mesh JWT both authenticate. Keep that window short; the cutover
    gate IS one SSM put-parameter.
* **Reversibility**
  - **Trivial down (back to X-Internal-Secret)** — re-enable the
    legacy fallback. Loses the per-service-isolation property; do
    not actually go back.
  - **Migration to mTLS is moderate** when the operability constraint
    relaxes (K8s + service mesh). The ES256 keys would still serve as
    the per-service identity assertion; mTLS would replace JWT-in-
    header with cert-in-handshake.

## Implementation references

* `sdk/common/auth.py:49-73` — library code + design comments
* `sdk/common/auth.py:22-24` — Prometheus counter `mesh_jwt_auth_total`
  for adoption tracking
* `scripts/ops/generate_mesh_keys.py` — keypair generation + SSM upload
* `docs/runbooks/secrets_rotation.md` §1 — rotation procedure
* OP-2 cutover row in `testing.md` — 14 keys minted to
  `/aegis-prodha/mesh/*` in SSM; compose-env wiring pending
* `infra/terraform/modules/params/main.tf` — SSM parameter declarations

## Verification

```bash
# 1. Confirm all 14 mesh keys exist in SSM (7 services × private + public,
#    plus the trusted-keys map).
aws ssm get-parameters-by-path --path /aegis-prodha/mesh/ --recursive \
  --region ap-south-1 --query 'length(Parameters)'
# expect: ≥ 15

# 2. Confirm tokens carry the expected audience + kid.
docker exec acp_gateway python3 - <<'PY'
import jwt, os, base64
priv = base64.b64decode(os.environ["ACP_MESH_PRIVATE_KEY_PEM"])
tok = jwt.encode({"aud":"acp.mesh.internal","sub":"gateway","exp":9999999999},
                 priv, algorithm="ES256", headers={"kid":"gateway"})
print(jwt.get_unverified_header(tok))
# expect: {'alg':'ES256','typ':'JWT','kid':'gateway'}
PY

# 3. Confirm mesh-JWT adoption rate via Prometheus.
curl -s http://prometheus:9090/api/v1/query \
  --data-urlencode 'query=sum by (method) (rate(mesh_jwt_auth_total[5m]))'
# expect: method=mesh_jwt dominates; method=legacy_secret close to 0.
```
