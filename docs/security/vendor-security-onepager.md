# Aegis — Vendor Security One-Pager

> Hand this to your procurement / vendor-security team. Every claim
> below is backed by code, runbook, or evidence artifact — links
> included. If a claim disagrees with what ships, the discrepancy
> surfaces in the same PR (see `docs/integrations/evidence-export.md`).

---

## Company

- **Vendor**: Aegis (operated by ByteHubble, Hyderabad).
- **Product**: Runtime governance for AI agents — pre-action policy
  enforcement + cryptographically verifiable audit chain.
- **Hosting region**: AWS `ap-south-1` (Mumbai). Cross-region replication
  to `ap-southeast-1` (Singapore) for backup-only artifacts.
- **Customer data residency**: All customer data stays in `ap-south-1`
  unless the customer explicitly enables cross-region replication.

## Posture summary

| Dimension                          | State |
|-----------------------------------|-------|
| Encryption in transit             | TLS 1.2+ everywhere (ALB enforced) |
| Encryption at rest                | RDS + EBS + S3 + ElastiCache, AWS-managed KMS minimum, customer-managed CMK supported |
| Signing keys                      | KMS / SSM SecureString — never on container filesystem in prod (see § "Signing key custody" below) |
| Secrets                           | AWS Secrets Manager, rotation per `scripts/ops/rotate_secrets.sh` |
| MFA on admin console              | Required for all root + IAM admin |
| Logging                           | CloudWatch + per-service Prometheus + the Aegis audit chain (tamper-evident) |
| SIEM forwarding                   | Splunk, Datadog, Elastic, Sentinel, Chronicle — see `docs/integrations/evidence-export.md` |
| DR — RTO / RPO                    | RDS 30m / 5m, Redis 5m / 0 (ephemeral), S3 1h / 0 — `docs/runbooks/dr.md` |
| Evidenced restore drill           | Weekly automated, signed JSON artifact, see `scripts/ops/dr_evidence.py` |
| SOC 2 Type I                      | Engaged with compliance-automation vendor — see § "SOC 2 status" |
| Penetration test                  | Commissioned for the reference deployment — see § "Pen-test scope" |
| SBOM                              | CycloneDX 1.5, signed with the ed25519 receipt key — `scripts/ops/generate_sbom.sh` |
| Vulnerability disclosure          | RFC 9116 — `https://aegisagent.in/.well-known/security.txt` |
| Public attack benchmark           | OWASP LLM Top-10 corpus (560 cases) — `tests/corpus/` + `tests/redteam/` |

## Signing key custody

Production deployments use one of two paths (Sprint 1.3 + Sprint 9):

1. **SSM SecureString** — the canonical path. The ed25519 private key
   lives at `/aegis-audit/receipt-signing-key`, encrypted under a
   customer-managed KMS CMK. The audit service reads it once at boot
   into memory; the key never touches container filesystem or logs.

2. **KMS envelope** — the ciphertext blob lives in S3 / env; the audit
   service decrypts under a customer-managed CMK at boot.

The local-file fallback is **REFUSED at process start** when
`AEGIS_ENV=prod` — see `sdk/common/signing_keys.py::provider_from_env`
and `tests/test_signing_keys_prod_guard.py`.

## Multi-tenancy isolation

- Tenant id is taken from the JWT claim, never the request header
  (`services/gateway/_mw_auth.py:L203-241`).
- Header MUST match the JWT claim or the gateway returns **403 Tenant
  mismatch detected** (`_mw_auth.py:L239-241`).
- Internal services reject any non-mesh traffic via
  `Depends(verify_internal_secret)`.
- The audit chain is per-tenant by `tenant_id` AND per-shard, so a
  cross-tenant tampering attempt fails the offline verifier.

## Pen-test scope

Engaged: **Q3 2026** (target).
- In scope: `https://ha.aegisagent.in` (the prod-ha environment from
  Sprint 9 once live), all `/api-keys/*`, `/auth/*`, `/execute`,
  `/audit/*`, `/receipts/*`, `/transparency/*`, `/policy/*`,
  `/graph/*`, `/shadow/*`, `/playground/*` endpoints.
- Tester IP allowlist: configurable via the WAFv2 `ip_allowlist_cidrs`
  variable in `infra/terraform/modules/waf` (Sprint 9 ships this).
- Findings are tracked in the SOC 2 evidence tracker (next section).

## SOC 2 status

- **Type I** engaged with a compliance-automation vendor (Vanta /
  Drata-class). Scoping doc in `docs/security/soc2_tracker.md`.
- Trust Services Criteria mapping: see the tracker for which Aegis
  evidence (audit chain, runbook, monitoring alert, code path) proves
  each control.

## Customer questionnaire short-circuits

| Common question | Where to find evidence |
|---|---|
| Are signing keys in HSM? | `sdk/common/signing_keys.py::AwsKmsSigningKeyProvider` + Sprint 9 prod-guard test |
| Multi-AZ for production? | `infra/terraform/environments/prod-ha/main.tf` — RDS multi-AZ, Redis replication group, ASG across 2 AZs |
| What's your RTO/RPO?      | `docs/runbooks/dr.md` §1 |
| Last successful restore?  | latest `reports/restore_drill/*.json` (or `s3://acp-backups-prodha-.../restore_drills/`) |
| Vulnerability disclosure? | `https://aegisagent.in/.well-known/security.txt` |
| SBOM?                     | `reports/sbom/aegis-merged-<sha>.json` + `.sig` |
| Attack coverage?          | `tests/corpus/` + `tests/redteam/` — OWASP LLM Top-10 corpus + redteam scenarios |

---

For any question this page doesn't answer:
**security@aegisagent.in** — replies within 48 hours.
