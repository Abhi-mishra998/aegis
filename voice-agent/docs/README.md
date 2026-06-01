# Aegis Documentation

**Aegis is a runtime governance and security control plane for AI agents.** This is the technical documentation for the platform — written for security engineers, integrators, and operators who need to know what the platform does, how it does it, and how to operate it.

## Start here

| If you are a … | Read |
|---|---|
| Security engineer evaluating Aegis | [What is Aegis?](introduction/what-is-aegis.md) then [Why Runtime Governance](introduction/why-runtime-governance.md) |
| Developer integrating an agent | [Quickstart](introduction/quickstart.md) then [API Reference](api/reference.md) |
| Architect doing a design review | [System Overview](architecture/system-overview.md) then [Flow of a Decision](architecture/flow-of-a-decision.md) |
| SRE responsible for the production deployment | [Deployment Topology](architecture/deployment-topology.md) then the [Runbooks](operations/runbooks/audit-chain-violation.md) |
| Compliance auditor | [Cryptographic Audit Chain](security/crypto-audit-chain.md) then [RBAC Roles](security/rbac-roles.md) |
| Product or business reader | [60-Second Tour](introduction/60-second-tour.md) |

## What's covered

- **Introduction (5 pages)** — what the platform is, what problems it solves, how to take a tour, and how the three demo packs populate the UI.
- **Architecture (7 pages)** — the system overview, the 11-stage gateway pipeline, the data model, multi-tenancy, deployment topology, UI primitives, and a worked end-to-end decision.
- **Voice Guide (3 pages)** — the voice-driven question-answering interface to this documentation: hybrid RAG (BM25 + dense + cross-encoder rerank), Groq-primary / Gemini-fallback LLM, deployed on a Graviton EC2 alongside the Aegis core.
- **Services (18 pages + index)** — every backend microservice documented to a 13-point spec: business purpose, architecture, request flow, dependencies, database tables, Redis usage, security controls, metrics, deployment model, API endpoints, example requests, troubleshooting, production considerations.
- **UI (34 pages + index)** — every page in the React UI documented to a 10-point spec: sidebar location, role gating, what you see, backend calls, auto-refresh and realtime, per-agent scoping, empty states, edge cases, related docs.
- **Security (7 pages)** — the cryptographic audit chain, JWT auth, RBAC, kill switch, OPA policies, threat scenarios, secret management.
- **Operations (6 main pages + 3 runbooks)** — deployment via tarball/S3/SSM, backup and restore drills, key rotation, soak tests, tenant data requests, observability, plus the three P0 runbooks.
- **API (4 pages)** — the full reference indexed from the live OpenAPI spec, authentication contract, error code matrix, copy-paste examples in curl / Python / Node.

## Platform at a glance

- **13 application services** + the `insight` HTTP + worker pair, running across **22 containers** on the live deployment.
- **1 EC2** host (`m6g.medium`, 4 GB Graviton) behind an Application Load Balancer at `https://dev.aegisagent.in`. The two-EC2 production footprint at `aegisagent.in` was decommissioned 2026-06-01; the dev environment is the only live deployment today.
- **9 logical Postgres databases** on RDS Single-AZ (`db.t4g.micro`).
- **Redis (ElastiCache `cache.t3.micro`)** for runtime state, rate limits, and pub/sub.
- **Cryptographic audit chain** — every decision is signed (ed25519), chained to the previous (SHA-256 prev_hash), and rolled into a daily Merkle root.
- **Kill switch** — tenant-wide halt propagates in under 5 seconds.
- **End-to-end gateway p95 ≈ 70 ms** on the live deployment (`/system/health` latency window, 60s).
- **Voice Guide** — a sibling EC2 (`t3.medium`, `ap-south-1`) hosts the LiveKit Agents worker that lets reviewers ask spoken questions against this documentation. Pipeline: Deepgram nova-3 → Groq llama-3.3-70b (Gemini fallback) → Cartesia sonic-3, grounded by hybrid retrieval over the same `docs/` tree. Documented under [Voice Guide](voice-guide/_index.md).

## Reading order if you have an hour

1. [What is Aegis?](introduction/what-is-aegis.md) — 5 minutes
2. [60-Second Tour](introduction/60-second-tour.md) — 5 minutes
3. [System Overview](architecture/system-overview.md) — 10 minutes
4. [Flow of a Decision](architecture/flow-of-a-decision.md) — 10 minutes
5. [Cryptographic Audit Chain](security/crypto-audit-chain.md) — 15 minutes
6. [Quickstart](introduction/quickstart.md) — 15 minutes (with a live deployment)

## How to verify the docs against the platform

Every page in this set cites real code paths (`services/*/*.py`, `services/policy/policies/*.rego`, `ui/src/pages/*.jsx`). A reviewer can cross-check claims:

```bash
# Verify the cited file exists and the claim is in it
grep -n "<claimed snippet>" <cited file>

# Verify the live audit chain is intact
curl -sS https://dev.aegisagent.in/audit/logs/verify \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" | jq

# Verify the running OpenAPI spec matches the reference
curl -sS https://dev.aegisagent.in/openapi.json | jq '.paths | keys | length'
```

Where this documentation and the running code disagree, the code wins. Please open an issue.

## Source layout

```
docs/
├── introduction/      # 4 pages
├── architecture/      # 6 pages
├── services/          # 18 service docs + index
├── ui/                # 34 UI docs + index
│   ├── primary/
│   ├── operations/
│   └── settings/
├── security/          # 7 pages
├── operations/        # 6 main + 3 runbooks
│   └── runbooks/
├── api/               # 4 pages
├── SUMMARY.md         # GitBook navigation
└── README.md          # this page
```

## License

Aegis is Apache 2.0. The documentation is CC-BY-4.0.

## Links

- Live demo: [dev.aegisagent.in](https://dev.aegisagent.in)
- Repository: [github.com/Abhi-mishra998/aegis](https://github.com/Abhi-mishra998/aegis)
- This documentation is also published at: (TBD when GitBook is live)
