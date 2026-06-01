# Secret Management

*Where secrets live in Aegis, how they are loaded, how they are rotated, and how they are kept out of logs and receipts.*

## Inventory

Every secret the platform uses, with its scope and lifetime:

| Secret | Used by | Storage | Lifetime |
|---|---|---|---|
| `JWT_SECRET_KEY` | identity (mint) and gateway (validate) | env var | Rotated quarterly or on compromise |
| `INTERNAL_SECRET` | every service (verify peer) | env var | Rotated quarterly |
| `RECEIPT_SIGNING_PRIVATE_KEY` (ed25519) | audit | env var (preferred) or `/data/keys/receipt-signing.pem` (fallback) | Rotated yearly with key promotion to historical keys table |
| `ROOT_SIGNING_PRIVATE_KEY` (ed25519) | audit | env var or `/data/keys/root-signing.pem` | Rotated yearly |
| Postgres passwords (per-database) | each service | env var | Rotated with `scripts/ops/rotate_db_passwords.sh` |
| `REDIS_URL` (carries the AUTH token for ElastiCache) | every service | env var | Rotated with ElastiCache replacement |
| `GROQ_API_KEY` | insight, groq_worker | env var | Rotated via Groq console |
| `SHODAN_API_KEY` and `ABUSEIPDB_API_KEY` | api (threat-intel) | env var | Rotated via provider console |
| SSO IdP client secrets | identity | Redis hash `acp:sso_config:{tenant_id}` | Per-tenant; rotated via the SSO Settings UI |
| Webhook secrets (Slack, PagerDuty, generic) | api | Postgres `acp_api.webhooks` | Per-tenant; rotated via Webhook Settings UI |
| SIEM forwarder tokens (Splunk HEC, Datadog API) | api | Postgres `acp_api.siem_connections` | Per-tenant; rotated via SIEM Settings UI |
| API keys (`acp_*` for SDK callers) | api | bcrypt hash in `acp_api.api_keys` (raw never stored) | Per-key; revoked via Developer Panel |
| User passwords | identity | bcrypt in `acp_identity.users.hashed_password` | Per-user; reset via SSO or admin update |

## Storage classes

### Class 1: env var only (never on disk, never in DB)

`JWT_SECRET_KEY`, `INTERNAL_SECRET`, and the receipt and root signing keys (when configured via the preferred env var path).

Properties:

- Loaded once at process start.
- Held in process memory.
- Never logged. Never returned in API responses.
- Cleared from memory only when the process exits.

The env var values come from the deployment mechanism — for the production deployment, they are set via the SSM document at deploy time and never persisted to disk on the EC2 host.

### Class 2: PEM file on a persistent volume

The receipt and root signing keys can fall back to `/data/keys/receipt-signing.pem` and `/root-signing.pem` if the env var is not set. Used in local development and as a safety net in production.

Production deployments should prefer the env var path so the EC2's disk does not hold the key. The fallback exists to keep the platform usable during a deploy that forgot to set the env var; an alert fires if the audit service starts up using the disk fallback.

### Class 3: bcrypt hash in Postgres

User passwords and API keys. The raw value enters the platform only at create time; the hash is stored. A leaked Postgres dump does not expose usable credentials.

The bcrypt cost factor is set at the application's default (12). The CPU cost of `bcrypt.checkpw` is non-trivial; identity runs it in a thread pool to avoid blocking the event loop. See [Identity service](../services/identity.md).

### Class 4: encrypted-at-rest in Postgres or Redis

SSO IdP client secrets and SIEM forwarder tokens. Encrypted with a per-tenant key derived from `INTERNAL_SECRET` plus a tenant salt. Decrypted on read. Never returned in full to API responses; UIs display `••••••` and only reveal a freshly-saved value once at submit time.

### Class 5: hash in Redis (revocation set)

`acp:revoked_tokens:{sha256(token)}`. The raw token is never stored — only its SHA-256 fingerprint. Revocation membership testing is constant-time and the set has a TTL matching the original token expiry.

## Loading precedence

Source: `services/audit/signer.py:42-55` for the signing key example. The same pattern applies to other secrets.

```
1. Environment variable (preferred — production)
2. File on persistent volume (fallback — dev / safety net)
3. Generated fresh in memory and warned (acceptable — tests only)
```

A service that ends up in path 3 logs a structured warning so an operator notices a misconfigured deploy. A service that ends up in path 3 in production has lost its signing key authenticity — the receipts it produces are not signed with the long-lived key.

## Rotation

### `JWT_SECRET_KEY`

1. Set the new secret in the deploy env vars for identity AND gateway.
2. Deploy identity (new tokens are minted with the new secret).
3. Deploy gateway (validates new tokens; old tokens 401).
4. Existing in-flight tokens 401 until users re-login. Plan rotations during low-traffic windows.

A C-5 mitigation (`services/gateway/auth.py:12`) cross-checks active tokens against an Identity-side `active_key` entry in Redis. A leaked `JWT_SECRET_KEY` alone does not let an attacker mint indefinite tokens because Identity's active_key entry would not match.

### `INTERNAL_SECRET`

Rotated by setting the new value on every service simultaneously. The platform supports a brief overlap window where both old and new secrets verify — set `INTERNAL_SECRET_PREVIOUS` to the old value during the transition.

### Receipt and root signing keys

The runbook is at [Key Rotation](../operations/key-rotation.md). Critical sequence:

1. Generate the new key pair.
2. Promote the old key's fingerprint to `transparency_historical_keys` BEFORE deploying the new key.
3. Deploy the new key.
4. New rows sign with the new key; old rows verify against the historical entry.

Skipping step 2 invalidates every receipt issued before the rotation. The runbook enforces the order.

### Postgres passwords

`scripts/ops/rotate_db_passwords.sh` automates the rotation:

1. Generate new passwords for each per-service role.
2. Update RDS and PgBouncer.
3. Update each service's `*_DB_PASSWORD` env var.
4. Roll services one at a time.

## Secret-in-log prevention

Three layers:

1. **Application code never logs secrets.** Audit reviews of `services/*/*.py` enforce this. The structured logger uses key-value pairs; secret-bearing keys (`password`, `token`, `secret`, `api_key`) are routed through a redactor.
2. **Stage 9 output filter redacts secrets** from API responses. Bearer tokens, API keys, and pattern-matched PII are redacted before the response leaves the gateway.
3. **Receipts do not embed secrets.** The canonical receipt JSON includes only the audit row content. Header values (Authorization, API keys) are not in the receipt.

## Secret in error messages

A common leak path. Aegis mitigates:

- Database errors are wrapped before being returned to the client. The wrapped error includes `error_type` but not the SQL parameters.
- HTTPX errors from downstream services are stripped to status code and a generic message. The downstream URL is preserved (for operator debugging) but query strings are masked.
- Validation errors include only the field path and the error class; the field value is not echoed back when the field is named `password`, `token`, `secret`, or `api_key`.

## Secrets in CI / CD

Aegis does not deploy via GitHub Actions (the deploy pipeline is tar → S3 → SSM; see [Deployment Topology](../architecture/deployment-topology.md)). Consequently:

- No GitHub-stored secrets touch the production EC2s.
- The EC2 instance role has S3 read for the deploy bucket and SSM agent permissions; nothing else.
- Deploy-time secrets are provided as SSM document parameters, not stored long-term.

## What to audit when reviewing a deployment

1. Confirm every Class-1 secret is set via env var, not via the fallback path. Check audit-service startup logs for the fallback warning.
2. Confirm `RECEIPT_SIGNING_PRIVATE_KEY` corresponds to a public-key fingerprint that appears on recent audit rows.
3. Confirm `transparency_historical_keys` is non-empty if any rotation has occurred.
4. Confirm `INTERNAL_SECRET` is identical across all services. A mismatch produces 401 on every internal call.
5. Inspect SSM document parameters at the latest deploy command. Confirm they are not stored anywhere persistent.

## What to do if you suspect a secret leak

| Secret | Immediate action |
|---|---|
| JWT_SECRET_KEY | Rotate immediately. Existing tokens 401 within seconds. Re-login required. |
| INTERNAL_SECRET | Rotate via overlap window. No client impact. |
| Receipt signing key | Promote old key to historical, rotate. New rows use new key. |
| Root signing key | Promote old key to historical, rotate. New roots use new key. |
| Postgres password | Rotate via the ops script. Brief connection blip during cutover. |
| API key (acp_*) | Revoke via Developer Panel. Subsequent uses 401. |
| Groq API key | Revoke via Groq console. Insights stop until new key deployed. |
| SSO client secret | Re-save via SSO Settings UI. Tenant's SSO logins fail until the IdP also has the new value. |

Every rotation produces an audit row (`action="secret_rotated"` or the specific kind). Post-rotation, verify the chain to confirm the rotation row is signed and chained.

## Next

- [Identity service](../services/identity.md) — owns JWT_SECRET_KEY and user passwords
- [Audit service](../services/audit.md) — owns the receipt and root signing keys
- [Key Rotation runbook](../operations/key-rotation.md) — the operator procedure
- [Deployment Topology](../architecture/deployment-topology.md) — how secrets reach the production EC2s
