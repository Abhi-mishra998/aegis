# Aegis Documentation

**Aegis is a runtime governance control plane for AI agents — and the open verification standard (AEVF) for the evidence those decisions produce.** This is the technical documentation for the platform, written for security engineers, integrators, operators, and the **auditors who will verify the evidence** without trusting us.

## The product promise — one sentence

> *"Don't trust us. Download the bundle, run the open verifier, prove the record wasn't altered — offline, no Aegis account, no API key, no network call."*

The [AEVF section](AEVF/README.md) is where that promise becomes operational: an open specification, an open verifier on PyPI, an auditor checklist, a reference audit report template, and a real signed example bundle that anyone can download and verify in 60 seconds.

## Start here

| If you are a … | Read |
|---|---|
| **Auditor / compliance practitioner** | [AEVF Overview](AEVF/README.md), then [Auditor Checklist](AEVF/auditor-checklist.md), then download the [Reference Evidence Package](AEVF/reference-bundle.md) |
| Security engineer evaluating Aegis | [What is Aegis?](introduction/what-is-aegis.md) then [Why Runtime Governance](introduction/why-runtime-governance.md) |
| Developer integrating an agent | [Quickstart](introduction/quickstart.md) then [API Reference](api/reference.md) |
| Architect doing a design review | [System Overview](architecture/system-overview.md) then [Flow of a Decision](architecture/flow-of-a-decision.md) |
| SRE responsible for the production deployment | [Deployment Topology](architecture/deployment-topology.md) then the [Runbooks](runbooks/audit_chain_violation.md) |
| GRC manager (Vanta / Drata / Secureframe user) | [Evidence Export](integrations/evidence-export.md) — `/compliance/export/grc?format=csv` produces rows your GRC platform ingests, each row carrying a back-reference to a verifiable AEVF bundle |
| Product or business reader | [60-Second Tour](introduction/60-second-tour.md) |
| Onboarding a new client / first-time install | [`setup-agies.md`](../setup-agies.md) at the repo root — long-form client onboarding guide, plus [`final-testing.md`](../final-testing.md) for 31/31 E2E PASS evidence |

## What's covered

- **AEVF — Open Verification Standard (5 pages)** — the specification at `aevf/0.1.0`, the friendly introduction, the auditor checklist, the reference audit-report template, and the reference evidence package. All Apache 2.0; an auditor can fork freely.
- **Introduction (5 pages)** — what the platform is, what problems it solves, how to take a tour, and how the three demo packs populate the UI.
- **Architecture (7 pages)** — the system overview, the 11-stage gateway pipeline, the data model, multi-tenancy, deployment topology, UI primitives, and a worked end-to-end decision.
- **Services (18 pages + index)** — every backend microservice documented to a 13-point spec.
- **UI (34 pages + index)** — every page in the React UI documented to a 10-point spec.
- **Voice Guide (4 pages)** — the voice-driven Q&A interface in the navbar (Deepgram → Groq → Cartesia + hybrid RAG).
- **Security (7 pages)** — cryptographic audit chain, JWT auth, RBAC, kill switch, OPA policies, threat scenarios, secret management.
- **Operations (6 main + 3 runbooks)** — deployment, backup/restore, key rotation, soak tests, tenant data requests, observability.
- **API (4 pages)** — full reference, authentication, error codes, copy-paste examples.

## Platform at a glance (current state)

- **Live URL:** `https://aegisagent.in` — clean canonical URL (the `ha.aegisagent.in` alias still resolves and is documented where the prod-ha topology is being named explicitly). Multi-AZ ASG of 2× `m6g.medium` Graviton behind ALB; Multi-AZ RDS Postgres; Redis replication group; WAFv2; KMS-rooted ed25519 signing keys in SSM SecureString.
- **16 application services across 22 containers.**
- **Cryptographic audit chain** — every decision signed with ed25519, chained via SHA-256 `prev_hash`, rolled into a daily Merkle root, **format published as the open AEVF spec at `/aevf/spec.md`**. The audit table is protected by an append-only DB trigger — no `UPDATE` / `DELETE` will even reach disk.
- **49 React UI pages** — every page wired to a live backend.
- **3 framework SDKs on PyPI — pinned to `==1.1.0`:** `aegis-anthropic`, `aegis-openai`, `aegis-langchain` — drop-in wrappers for Anthropic / OpenAI / LangChain agents. See [SDK 1.1.0 Release](integrations/sdk-1.1.0-release.md) for the changelog and pin-line.
- **Streaming control-plane events:** the SSE channel surfaces four new event types — `decision.upserted`, `incident.opened`, `incident.closed`, and `mitre.coverage.updated` (per-agent MITRE coverage in the Threat Graph).
- **Compliance coverage** — signed verifiable bundles for **SOC 2, EU AI Act, NIST AI RMF, and India DPDP Act 2023** (with Rules 2025-11-13); GRC export shaped for Vanta / Drata / Secureframe / Hyperproof; AEVF back-reference on every SIEM event and every GRC row.
- **Live demo at `/live-demo`** — scenario picker (fintech_data_egress / devops_destruction / support_pii_exfil), buyer-editable prompts, every deny earned from action semantics across all risk levels.
- **Minimal self-host mode** at `infra/minimal/` — 3 docker services (aegis-core + postgres + redis), validated 10/10 on a throwaway EC2.
- **Voice Agent in the navbar** — Deepgram nova-3 → Groq llama-3.3-70b (Gemini fallback) → Cartesia sonic-3, hybrid RAG over **1 794 chunks from 103 docs**.

## Reading order if you have an hour

1. [What is Aegis?](introduction/what-is-aegis.md) — 5 minutes
2. [AEVF Overview](AEVF/README.md) — 5 minutes
3. [60-Second Tour](introduction/60-second-tour.md) — 5 minutes
4. [System Overview](architecture/system-overview.md) — 10 minutes
5. [Flow of a Decision](architecture/flow-of-a-decision.md) — 10 minutes
6. [Cryptographic Audit Chain](security/crypto-audit-chain.md) — 15 minutes
7. [Quickstart](introduction/quickstart.md) — 15 minutes (with a live deployment)

## Reading order for an auditor (25 minutes, before the engagement)

1. [AEVF Overview](AEVF/README.md) — 5 minutes
2. [AEVF Specification](AEVF/spec.md) — skim the V1–V6 verification algorithm, ~10 minutes
3. [Auditor Checklist](AEVF/auditor-checklist.md) — ~5 minutes
4. Download the [Reference Evidence Package](AEVF/reference-bundle.md), `pip install aegis-aevf`, run it offline — ~5 minutes

## How to verify the docs against the platform

Every page in this set cites real code paths. A reviewer can cross-check claims:

```bash
# Verify a cited file exists and the claim is in it.
grep -n "<claimed snippet>" <cited file>

# Pull the public AEVF spec — auditors do this without an account:
curl -sS https://aegisagent.in/aevf/spec.md | head -20

# Download the reference bundle and verify it offline:
curl -O https://aegisagent.in/aevf/reference-bundle-2026-06.json
sha256sum reference-bundle-2026-06.json
# → 8a6f09f65c374edf44c811dba8f146c8d79dab9ed74e3c49920be759951f20fc
pip install 'aegis-aevf==1.1.0'
aegis-verify --bundle reference-bundle-2026-06.json
# → 6/6 PASS

# Verify the running OpenAPI spec matches the reference.
TOKEN=...
curl -sS https://aegisagent.in/openapi.json | jq '.paths | keys | length'
```

Where this documentation and the running code disagree, the code wins. Please open an issue.

## Source layout

```
docs/
├── AEVF/                # The open standard — spec, README, checklist, report, bundle
├── introduction/        # 5 pages
├── architecture/        # 7 pages
├── services/            # 18 service docs + index
├── ui/                  # 34 UI docs + index
├── security/            # 7 pages
├── operations/          # 6 main + 3 runbooks
├── integrations/        # SDK + evidence-export adapter docs
├── api/                 # 4 pages
├── voice-guide/         # 4 pages
├── SUMMARY.md           # GitBook navigation
└── README.md            # this page
```

## License

- Aegis source code: **Apache License 2.0**
- AEVF specification: **Apache License 2.0** (fork the spec, write your own verifier)
- This documentation: **CC-BY-4.0**

## Links

- **Live URL:** [aegisagent.in](https://aegisagent.in) (clean canonical URL; `ha.aegisagent.in` alias still resolves)
- **AEVF landing:** [aegisagent.in/aevf/](https://aegisagent.in/aevf/)
- **Reference verifier on PyPI (1.1.0):** [`pip install 'aegis-aevf==1.1.0'`](https://pypi.org/project/aegis-aevf/)
- **Framework SDKs (1.1.0):** [`pip install 'aegis-anthropic==1.1.0'`](https://pypi.org/project/aegis-anthropic/) · [`pip install 'aegis-openai==1.1.0'`](https://pypi.org/project/aegis-openai/) · [`pip install 'aegis-langchain==1.1.0'`](https://pypi.org/project/aegis-langchain/)
- **Client onboarding (long-form):** [`setup-agies.md`](../setup-agies.md) at the repo root
- **E2E PASS evidence:** [`final-testing.md`](../final-testing.md) at the repo root (31/31 PASS)
- **Repository:** [github.com/Abhi-mishra998/aegis](https://github.com/Abhi-mishra998/aegis)
- **Security disclosures:** [`SECURITY.md`](https://github.com/Abhi-mishra998/aegis/blob/main/SECURITY.md)
