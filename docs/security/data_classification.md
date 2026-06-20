# Aegis data classification

Every byte that flows through Aegis falls into one of four buckets. The classification dictates encryption, retention, access logging, and export rules.

## The four buckets

| Class | Definition | Examples in Aegis | Encryption | Retention | Cross-region replication |
|-------|------------|-------------------|------------|-----------|--------------------------|
| **Public** | Designed for unrestricted disclosure. | `/.well-known/security.txt`, the cryptographic-transparency Merkle roots at `s3://aegis-public-roots-…`, scenarios listed at `/demo/scenarios`. | Integrity-signed (ed25519) but not encrypted at rest. | Indefinite. | Cross-region replicated by design. |
| **Internal** | Aegis operational data, no customer-identifying content. | Prometheus metrics (`acp_*_total`), gateway logs without PII, alert rule definitions, code. | TLS in transit; AES-256 at rest in S3/RDS. | Standard ops retention (30–90 days). | Standard. |
| **Confidential** | Customer-identifying business data. The default class for everything customers send through the platform. | Audit log rows, agent definitions, policy packs, incident records, dashboards, billing line-items, decision events. | TLS 1.2+ in transit; AES-256 in RDS + ElastiCache + S3; KMS-CMK for audit envelope. | Per-tenant retention setting (default 365 days for audit, 90 days for ops). | Backup snapshot to DR region only — never cross-region replicated live. |
| **Restricted** | Cryptographic keys, raw secrets, employee personal data. | Receipt signing private key (SSM SecureString), per-service mesh JWT private keys, Clerk session tokens, employee PII (email, role). | AES-256 with **per-key KMS CMK**; access logged to CloudTrail with mandatory alert. | Lifetime of the workspace + 30 days; cryptographic keys rotated per `docs/runbooks/secrets_rotation.md`. | NEVER cross-region replicated live; backed up via age-encrypted nightly dump. |

## Where each class lives

```
PUBLIC      → s3://aegis-public-roots-…           (anonymous read)
              ui/public/.well-known/*             (served by nginx)
INTERNAL    → Prometheus / Loki / Grafana         (VPC-internal only)
              acp_audit.metrics_*                 (internal queries)
CONFIDENTIAL→ acp_audit, acp_identity, acp_*      (per-tenant scoped queries)
              ElastiCache (with TLS + per-key prefixes)
              s3://aegis-prod-backups-…           (Object Lock 30d governance)
RESTRICTED  → AWS Secrets Manager (per-key KMS CMK)
              AWS SSM Parameter Store SecureString
              EC2 instance memory only — never persisted to local disk
```

## Operator obligations per class

- **Public** — verify integrity signature before publishing; never include PII.
- **Internal** — Prometheus must scrape via `X-Internal-Secret`; never expose `/metrics` to the public ALB (verified in `services/gateway/middleware.py`).
- **Confidential** — every SQL query MUST include `WHERE tenant_id = $1`. The RBAC matrix at `docs/security/rbac_matrix.md` enumerates which role can read which Confidential data. Cross-tenant access is structurally impossible (proved via `reports/e2e_test_2026_06_20/isolation_test.sh`).
- **Restricted** — only `instance-role`-authenticated services may read SSM/Secrets Manager. CloudTrail logs every access. Suspected leak triggers the procedure in `docs/runbooks/secrets_rotation.md §5`.

## Customer-facing exports

The compliance-export endpoint (`POST /compliance/export`) bundles Confidential data into a customer-encrypted ZIP. The customer's encryption key is derived from their OWNER session token + a per-export nonce, so the ZIP at rest in S3 is unreadable to Aegis operators.

## How to challenge a classification

If a customer believes a specific data column is mis-classified (e.g. a value Aegis treats as Internal that should be Confidential under their contract), open a Customer Security ticket. The default-classification table above governs unless overridden in writing.
