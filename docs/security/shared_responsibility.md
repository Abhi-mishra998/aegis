# Aegis shared-responsibility model

Aegis sits between AI agents and the systems they control. Some controls are Aegis's responsibility; some are the customer's. Mis-attribution is the most common source of post-incident finger-pointing. This document is the contract.

## At a glance

| Layer | Aegis | Customer |
|-------|:-----:|:--------:|
| Platform — hosting, OS patching, network isolation | ✅ | — |
| Platform — tenant isolation, RBAC, audit chain integrity | ✅ | — |
| Platform — TLS termination, WAF, DDoS mitigation | ✅ | — |
| Cryptographic transparency log + offline verifier | ✅ | — |
| **Agent definitions + allowed tools** | — | ✅ |
| **Policy pack selection + customisation** | shipped defaults | activate / extend |
| **LLM API key custody** | per-tenant vault available | provide the key |
| **Employee identity (Clerk org)** | platform integration | onboard / off-board |
| **Slack / SIEM / webhook integrations** | platform integration | configure + maintain webhook URL |
| **Approval workflow routing** | platform integration | configure approvers |
| **Encrypted backup of customer data** | nightly snapshot to S3 (Object Lock) | export decisions for off-platform retention |
| **Compliance evidence export** | tooling: `POST /compliance/export` | run on customer's cadence |

## Aegis is responsible for

- **Tenant isolation** — every SQL query is `WHERE tenant_id = $1`-filtered (verified in `reports/e2e_test_2026_06_20/isolation_test.sh`). Cross-tenant access blocked at the data layer, not just the API.
- **Authentication + role authorization** — Clerk session → canonical role → `services/gateway/_rbac_map.py`. Documented in `docs/security/rbac_matrix.md`; tested in `tests/test_rbac_matrix.py`.
- **Cryptographic audit chain** — every decision row signed with ed25519, Merkle-rolled daily, root published to `s3://aegis-public-roots-628478946931/`. Append-only DB trigger blocks UPDATE/DELETE.
- **Encryption** — TLS 1.2+ in transit, AES-256 at rest (RDS, ElastiCache, S3), per-tenant KMS CMK for the audit envelope.
- **Vulnerability + supply-chain scanning** — every PR runs Trivy + Gitleaks + Checkov + Bandit. Bundles signed with cosign keyless OIDC.
- **Disaster recovery** — RDS Multi-AZ + nightly age-encrypted backups + cross-region snapshot copy. RTO/RPO in `docs/runbooks/disaster_recovery.md`.
- **Operational monitoring** — Prometheus + AlertManager + on-call rotation. Status page published every minute to `https://status.aegisagent.in/`.
- **Security operations** — auth-failure spike, tenant-isolation-violation, RBAC-deny-spike, mass-export, revoked-token-storm alerts (Sprint EH-3). PagerDuty escalation per `docs/runbooks/disaster_recovery.md §on-call`.

## Customer is responsible for

- **Agent design** — what tools each agent is allowed to call. Default-deny applies, but customers must explicitly grant capabilities.
- **Policy pack activation** — Aegis ships SOC2, HIPAA, PCI, Finance, DevOps packs. Customer chooses which to enable.
- **LLM API key safeguarding** — if you provide your own Anthropic/OpenAI key, you're responsible for its rotation and revocation at the provider. Aegis stores keys in AWS Secrets Manager with KMS CMK encryption.
- **User lifecycle** — onboarding (invite via `POST /auth/users`) and off-boarding (revoke via `DELETE /auth/users/{id}`) happens in your Clerk org. Aegis honours Clerk's user state.
- **Slack / SIEM webhook configuration** — Aegis provides the connectors; customer configures the destination URL + signing secret.
- **Approval workflow approvers** — Aegis routes; customer assigns who approves what.
- **Backup retention for compliance** — Aegis backs up for operational restore; for long-term compliance evidence the customer exports via `POST /compliance/export`.
- **Incident response triage on customer-controlled signals** — Aegis surfaces incidents via the Inbox; the customer decides on remediation actions on their own systems.

## Joint responsibility

- **Pen testing** — Aegis engages annual third-party pen tests of the platform (next: Q3 2026 per `docs/security/pentest-sow-template.md`). Customers are expected to pen-test their own agents + tool configurations.
- **Audit log review** — Aegis surfaces, customer reads + acts.
- **Security incident communication** — Aegis notifies affected customers within 24 hours of detection per the security incident response plan; customer notifies their own users + regulators per their own policies.

## Out-of-scope

- **Tool execution outcomes** — Aegis evaluates the proposed action, decides allow/deny/escalate, then either invokes the tool or blocks. Aegis does NOT guarantee correctness of the underlying tool (e.g. if your `wire_transfer` tool sends money to the wrong recipient, that's the tool's bug not Aegis's).
- **LLM hallucinations** — Aegis governs WHAT actions reach systems; if a model fabricates a `read_csv` argument, Aegis evaluates the fabricated arg, not the model's reasoning.
- **Customer agent secrets** — credentials your agent uses for its own tools (DB password, API keys for the systems it operates) are out of scope. Aegis sees the tool call, not the credentials the tool uses internally.

## How to invoke the model in conversation

> "Aegis is responsible for tenant isolation, RBAC, audit chain, and platform encryption. We are responsible for agent definitions, policy pack selection, LLM key custody, and downstream tool correctness. Joint: pen testing, audit log review, incident communication."

Print and pin somewhere visible during a sales conversation. It saves 20 minutes of architecture-diagram drawing.
