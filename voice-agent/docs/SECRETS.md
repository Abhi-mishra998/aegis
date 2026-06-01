# Secret Management

## Secrets inventory

| Secret | Scope | Rotation interval | Where used |
|--------|-------|------------------|------------|
| `INTERNAL_SECRET` | Inter-service mesh auth | 30 days | All 20+ services; gateway validates inbound requests from other services |
| `JWT_SECRET_KEY` | User/agent JWT signing | 7 days | Gateway JWT validation, identity service token issuance |
| `GROQ_API_KEY` | External LLM API | On compromise | Insight service only |
| `POSTGRES_PASSWORD` | Database | 90 days | PgBouncer + all DB-connected services |
| `ACP_AGE_PRIVATE_KEY` | Backup encryption | 180 days | `scripts/ops/backup.sh` decryption |
| Transparency signing key | Audit root signing | 90 days | `services/audit/transparency_signer.py` |

---

## How secrets are loaded

ACP uses **pydantic-settings** (`sdk/common/config.py`). All fields marked `Field(...)` are **required** — the process will refuse to start if any are absent. Values are read in priority order:

1. Environment variables (highest priority)
2. `.env` file in the working directory
3. AWS Secrets Manager (see below)

### AWS Secrets Manager integration

Set `ACP_SECRETS_MANAGER_ARN` to the ARN of a Secrets Manager secret containing a JSON object with any subset of the config keys:

```bash
export ACP_SECRETS_MANAGER_ARN=arn:aws:secretsmanager:ap-south-1:123456789012:secret:acp/prod
```

The gateway's startup hook (`services/gateway/main.py`, `lifespan`) will fetch and merge the values before pydantic-settings finalises the config. Individual environment variables still override Secrets Manager values, so per-instance overrides remain possible.

### Vault integration (HashiCorp)

Set `ACP_VAULT_ADDR` + `ACP_VAULT_TOKEN` (or `ACP_VAULT_ROLE_ID` + `ACP_VAULT_SECRET_ID` for AppRole). ACP will read from the `secret/data/acp/prod` path at startup:

```bash
export ACP_VAULT_ADDR=https://vault.internal
export ACP_VAULT_ROLE_ID=...
export ACP_VAULT_SECRET_ID=...
```

### Docker / local development

For local dev and CI, use a `.env` file that is **never committed** (it is in `.gitignore`). The `infra/docker-compose.yml` passes secrets via `environment:` blocks; in production replace these with Docker Secrets or ECS task-definition secrets.

---

## INTERNAL_SECRET — threat model

`INTERNAL_SECRET` is a 32-byte hex string shared across all services. It is used as a `Bearer` token on the `X-Internal-Auth` header for inter-service requests.

**Attack surface**: anyone with `docker exec` access to a running container can read the secret from environment variables.

**Mitigations in production**:
- Run containers as non-root with `read-only` filesystem (only `/tmp` writable)
- Restrict `docker exec` to deployment automation only (no developer SSH to prod)
- Use short-lived container identities (ECS task roles / K8s service accounts) instead of a static shared secret where possible
- Rotate every 30 days via the key rotation runbook

**Roadmap**: replace the shared secret with mTLS service mesh (e.g. Istio/Linkerd) so no secret is needed at the application layer. Tracked in the backlog.

---

## Audit trail

Every secret rotation must be logged in `docs/runbooks/key_rotation_drill_log.md`.
