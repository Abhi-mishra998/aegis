# Aegis subprocessors

Every third party that processes customer data on Aegis's behalf. Updated whenever a vendor changes; customers are notified 30 days before any addition that materially expands data exposure.

| Vendor | Purpose | Data shared | Region (ap-south-1 instance) | Region (eu-west-1 instance) | Compliance |
|--------|---------|-------------|------------------------------|------------------------------|------------|
| **AWS** | Hosting (EC2, RDS Multi-AZ, ElastiCache, S3, KMS, ALB, WAF, CloudTrail) | All customer data (encrypted at rest + in transit) | `ap-south-1` (Mumbai), DR plan covers `ap-southeast-1` | `eu-west-1` (Ireland), full data isolation — see `docs/security/data_residency.md` | SOC 1/2/3, ISO 27001/27017/27018, PCI-DSS L1, HIPAA BAA available |
| **Anthropic** | LLM inference for the `/v1/messages` proxy | Prompts + completions for tenants who use the Anthropic-compatible proxy. Per-tenant API keys available — see `docs/runbooks/secrets_rotation.md §3` | US (`api.anthropic.com`) | EU (`api.eu.anthropic.com`) — opt-in per-tenant | SOC 2 Type II, ISO 27001 |
| **OpenAI** | LLM inference for `/v1/chat/completions` proxy (opt-in only) | Same as Anthropic — prompts + completions for tenants opted in | US (`api.openai.com`) | EU via Azure OpenAI EU regions — per-tenant DPA | SOC 2 Type II |
| **Clerk** | User authentication + organization management | User email, name, OAuth identity tokens | US (with EU + APAC regional options) | EU-pinned Clerk organization — separate `auth.eu.aegisagent.in` tenant | SOC 2 Type II, GDPR DPA, CCPA |
| **Stripe** | Billing + payment processing | Cardholder data is tokenized — Aegis never touches the PAN. Email + subscription metadata exchanged. | US + EU (Stripe routes per customer country) | EU entity used for EU customers — no Aegis-side config change | PCI-DSS L1, SOC 1/2, ISO 27001, GDPR DPA |
| **GitHub** | Source repository + CI/CD (security scanning, Cosign signing OIDC) | No customer data — only Aegis source + build artifacts | US | US — same; only build artifacts cross, no customer data | SOC 1/2/3, ISO 27001 |
| **Sigstore (Fulcio + Rekor)** | Bundle-signing certificate authority + transparency log | Cryptographic signature metadata on every signed bundle (no customer data) | Multi-region | Multi-region — same | Operated by The Linux Foundation OpenSSF |

## What changed in the last 6 months

- 2026-06-20 — added eu-west-1 (Ireland) regional posture for every sub-processor in preparation for the EU instance launch (Sprint EI-5). No new vendors added; existing vendors' EU residency stories enumerated.
- 2026-06-19 — added Sigstore (Fulcio + Rekor) when cosign signing was introduced in Sprint EH-4.
- 2026-06-15 — Clerk added (replaced an in-house bcrypt password flow).

## How customers are notified

Material changes are announced via:
1. Email to the OWNER role on every tenant.
2. A signed entry in `s3://aegis-public-roots-628478946931/announcements/`.
3. An updated commit to this file in the public repo, so the diff is auditable.

A 30-day notice window is observed for any subprocessor that gets first-time access to a new data class (e.g. adding a new LLM provider). No-notice updates are permitted only for SaaS vendor upgrades within the same data class (e.g. AWS adding a new compliance attestation).
