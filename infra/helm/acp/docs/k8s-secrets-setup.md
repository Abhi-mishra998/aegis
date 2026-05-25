# ACP Kubernetes Secrets Setup

This document describes the secrets that must exist in the cluster **before** running
`helm install` or `helm upgrade`. ACP uses the External Secrets Operator (ESO) when
`externalSecrets.enabled=true` (recommended for production), or pre-created secrets
when `externalSecrets.enabled=false`.

---

## Required Secrets

### 1. `acp-secrets` — Application Secrets

| Key | Description | How to generate |
|-----|-------------|-----------------|
| `JWT_SECRET_KEY` | HMAC-SHA256 signing key for all issued JWTs | `openssl rand -base64 32` |
| `INTERNAL_SECRET` | Shared secret for service-to-service authentication | `openssl rand -base64 32` |
| `GROQ_API_KEY` | Groq LLM API key (optional — omit if not using Groq) | From https://console.groq.com |

### 2. `acp-db-credentials` — PostgreSQL

| Key | Description |
|-----|-------------|
| `username` | Database username |
| `password` | Database password |
| `DATABASE_URL` | Full asyncpg connection URL, e.g. `postgresql+asyncpg://user:pass@host:5432/acp` |

### 3. `acp-redis-credentials` — Redis

| Key | Description |
|-----|-------------|
| `REDIS_URL` | Full Redis URL, e.g. `redis://:password@host:6379/0` |

---

## Manual Creation (externalSecrets.enabled=false)

Run these commands once per cluster namespace before deploying the Helm chart.

```bash
# Namespace (create if it doesn't exist)
kubectl create namespace acp

# 1. Application secrets
JWT_SECRET_KEY=$(openssl rand -base64 32)
INTERNAL_SECRET=$(openssl rand -base64 32)
GROQ_API_KEY="gsk_your_groq_key_here"   # Replace with your actual key

kubectl create secret generic acp-secrets \
  --namespace acp \
  --from-literal=JWT_SECRET_KEY="${JWT_SECRET_KEY}" \
  --from-literal=INTERNAL_SECRET="${INTERNAL_SECRET}" \
  --from-literal=GROQ_API_KEY="${GROQ_API_KEY}"

# 2. Database credentials (replace with your RDS/CloudSQL endpoint)
DB_HOST="your-rds-endpoint.region.rds.amazonaws.com"
DB_USER="acp_prod"
DB_PASS="$(openssl rand -hex 20)"
DB_NAME="acp"

kubectl create secret generic acp-db-credentials \
  --namespace acp \
  --from-literal=username="${DB_USER}" \
  --from-literal=password="${DB_PASS}" \
  --from-literal=DATABASE_URL="postgresql+asyncpg://${DB_USER}:${DB_PASS}@${DB_HOST}:5432/${DB_NAME}"

# 3. Redis credentials (replace with your ElastiCache/MemoryStore endpoint)
REDIS_HOST="your-redis-endpoint.region.cache.amazonaws.com"
REDIS_PASS="$(openssl rand -hex 20)"

kubectl create secret generic acp-redis-credentials \
  --namespace acp \
  --from-literal=REDIS_URL="redis://:${REDIS_PASS}@${REDIS_HOST}:6379/0"
```

Verify:
```bash
kubectl get secrets -n acp
# Expected: acp-secrets, acp-db-credentials, acp-redis-credentials
```

---

## External Secrets Operator (externalSecrets.enabled=true)

For production deployments, use ESO to sync secrets from HashiCorp Vault, AWS Secrets Manager,
or GCP Secret Manager. ESO rotates secrets automatically at the configured `refreshInterval`.

### Install ESO

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets --create-namespace
```

### Configure a SecretStore (example: AWS Secrets Manager)

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: acp-vault-store
spec:
  provider:
    aws:
      service: SecretsManager
      region: us-east-1
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: external-secrets
```

### Expected vault secret paths

When using the default `remoteRefPrefix: secret/acp/prod`:

| Vault path | Properties expected |
|------------|-------------------|
| `secret/acp/prod/jwt` | `secret_key` |
| `secret/acp/prod/internal` | `secret` |
| `secret/acp/prod/groq` | `api_key` |
| `secret/acp/prod/postgres` | `username`, `password`, `database_url` |
| `secret/acp/prod/redis` | `redis_url` |

---

## TLS Certificate

The Ingress references `acp-tls` (or `acp-tls-prod` in production).

**Option A — cert-manager (recommended):**
```bash
helm install cert-manager jetstack/cert-manager \
  -n cert-manager --create-namespace \
  --set installCRDs=true

# Then add annotation to ingress in values.prod.yaml:
# cert-manager.io/cluster-issuer: letsencrypt-prod
```

**Option B — Manual:**
```bash
kubectl create secret tls acp-tls \
  --namespace acp \
  --cert=/path/to/tls.crt \
  --key=/path/to/tls.key
```

---

## Security Notes

- Never commit plaintext secrets to version control.
- Rotate `JWT_SECRET_KEY` using the key rotation runbook: `docs/runbooks/key_rotation.md`.
- Rotate `INTERNAL_SECRET` requires a coordinated rolling restart of all services.
- `DATABASE_URL` contains credentials — treat it as a secret, not config.
- For PCI/SOC 2 environments, all secrets must have automated rotation enabled (ESO + Vault dynamic secrets or AWS Secrets Manager rotation).
