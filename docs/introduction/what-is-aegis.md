# What is Aegis?

**Aegis is a runtime governance control plane for AI agents — and the open verification standard (AEVF) for the evidence those decisions produce.**

Every agent action — executing SQL, invoking an API, modifying cloud infrastructure, accessing customer data — is evaluated before it reaches the target system. Aegis applies authentication, policy controls, behavioral analysis, rate limits, autonomy constraints, and per-tenant governance rules in real time. Actions that violate policy are blocked at the gateway. Allowed actions pass through with an end-to-end p95 of about 34 ms on the prod-ha deployment (2× `m6g.medium` Graviton ASG behind ALB, Multi-AZ); the number is measured by `scripts/qa/test_prodha.py`.

Every decision — allowed or denied — is recorded in a tamper-evident cryptographic audit chain. Each row is signed (ed25519), linked to the previous row by hash, and rolled into a daily Merkle transparency root. The bundle format and verification algorithm are published as an open standard — **[AEVF](../AEVF/README.md), version `aevf/0.1.0`** — so an auditor can verify the evidence offline, with the reference implementation from PyPI (`pip install aegis-aevf`), **without trusting the vendor that produced it**.

> **The product promise — one sentence:**
> *"Don't trust us. Download the bundle, run the open verifier, prove the record wasn't altered — offline, no Aegis account, no API key, no network call."*

## What it does

Aegis sits between an AI agent and the systems the agent can touch. Every call goes through an eleven-stage gateway pipeline, numbered 0 through 10:

0. **Kill switch** — checks a tenant-wide halt flag; propagates in under 5 seconds.
1. **Authentication** — JWT validation, revocation check, JTI replay window, role gate.
2. **Rate limiting** — per-tenant requests-per-second, burst, daily and monthly caps, USD cost caps.
3. **Inference proxy** — prompt-injection scan, tool-name guard, request-shape risk scoring.
4. **Policy evaluation** — Redis-cached OPA Rego rules plus hard-deny patterns (path traversal, destructive SQL, k8s prod-namespace operations).
5. **Behavioral firewall** — sequence, velocity, cost, and cross-agent anomaly scoring with degraded-mode fallback.
6. **Decision engine** — combines stages 3, 4, 5 into one action; emits canonical findings vocabulary.
7. **Enforcement + autonomy contract** — maps decision to ALLOW / MONITOR / THROTTLE / ESCALATE / KILL; cross-tenant rules, time windows, delegation caps.
8. **Execution** — proxies to the target tool, captures the result.
9. **Output filter** — redacts secrets from the response body.
10. **Audit + billing** — writes the signed audit row and records usage atomically via the outbox pattern.

If any stage denies, execution does not happen. The decision is still recorded, and the caller receives a structured 403 explaining which rule fired.

## Why it exists

AI agents now write code, run queries, send messages, and change production infrastructure. The same agent that summarizes an inbox can, through prompt injection in an incoming email, exfiltrate that inbox to an attacker. The same agent that scales a deployment can delete a namespace. Static code review and post-hoc log analysis do not catch these. The decision to act happens at runtime, inside the model, in milliseconds.

Two capabilities most teams do not get from their LLM provider or their existing security stack:

1. **Runtime authorization** — the ability to deny an action at the last possible moment, even after the model has already decided to take it.
2. **Tamper-evident audit** — a cryptographically verifiable record of every approved and denied action, suitable for SOC 2, EU AI Act, NIST AI RMF, internal forensics, and regulatory reporting.

Aegis provides both. It runs as a service in front of the agents. Agents call it for every action. It decides, records, and proves.

## What's included

- **Gateway** — the 11-stage decision pipeline (FastAPI + OPA + Redis).
- **Cryptographic audit chain** — ed25519 signing, prev-hash chaining, daily Merkle transparency root, receipt verification API, consistency proofs across roots.
- **Kill switch architecture** — tenant-scoped, engaged from the UI or API, propagates to every gateway worker in under 5 seconds.
- **Per-tenant and per-agent quotas** — requests-per-second, daily and monthly request caps, USD-denominated inference cost caps with one-shot 80% warnings and 100% blocks.
- **Identity graph** — agents, tools, resources, tenants, and humans are typed nodes; every action is a typed edge (`invokes`, `reads`, `writes`, `delegates`, `escalates`). Powers blast-radius analysis and what-if compromise simulation.
- **Flight Recorder** — full per-execution timeline of which stage decided what, signed end-to-end, with snapshot capture pre- and post-decision.
- **Forensics** — investigation listing, replay, blast-radius, timeline, and PDF export.
- **Behavioral firewall** — per-tenant degraded-mode policy (`block_high_risk` / `block_all` / `allow_with_audit`) with unconditional audit emission on every consult.

## Reference deployment

The current live deployment is the **prod-ha environment** at `ha.aegisagent.in`, cut over 2026-06-13. Multi-AZ HA stack. The earlier single-EC2 dev environment (formerly at `dev.aegisagent.in`) and the 2026-06-01 single-EC2 reference have both been folded into this one URL.

- **2× EC2 ASG** (`m6g.medium` Graviton, 1 vCPU / 4 GB each) across `ap-south-1a + 1b` behind an Application Load Balancer
- **22 containers per instance**: 16 application services + Postgres (pgbouncer fronted), Redis, OPA, bundle server, Prometheus, Grafana, Jaeger, Alertmanager
- **Postgres (RDS Multi-AZ, `db.t3.small`)** for application state, **Redis (ElastiCache replication group, primary + reader)** for caches and Pub/Sub, **S3** for receipts and tenant exports
- **WAFv2** in front (Common rules + KnownBadInputs + SQLi + per-IP rate limit), **KMS-rooted ed25519 signing keys** in SSM SecureString

## What's measured on the reference deployment

- **End-to-end p95 latency**: ~34 ms (`/system/health`, measured by `scripts/qa/test_prodha.py`)
- **Kill switch propagation**: per-request Redis check (sub-second) plus a 30 s rehydration loop that restores the flag if Redis is flushed; see [Kill Switch](../security/kill-switch.md)
- **Attack-scenario block rate**: 100% on the four scripted demo payloads (PII bulk export, `rm -rf`, `DROP TABLE`, k8s namespace delete). Generalization beyond those exact strings is in progress (see Sprint 2 in the roadmap)
- **Audit chain integrity violations**: 0 in the live chain (5,409+ decisions)
- **Test suite**: ~1,322 unique `def test_` functions across `tests/` and `services/*/tests/`

## Who it's for

- **Security teams** running AI agents in production who need prompt-injection blocking and runtime tool-call denial without modifying the model or the application code.
- **Compliance teams** preparing for SOC 2, EU AI Act, or NIST AI RMF audits who need a cryptographically verifiable log of every AI action and a defensible chain of custody.
- **Platform engineering teams** giving internal agents access to databases, customer data, and cloud control planes who need per-agent guardrails enforced at the call site, not just at code-review time.

## What it isn't

- **Not an LLM.** Aegis does not generate text or replace your model. It governs what the model is allowed to do once it has decided to do it.
- **Not a content filter.** Aegis evaluates actions, not message content. Tone, sentiment, and PII-in-strings are out of scope.
- **Not a static policy reviewer.** Enforcement is at runtime, on the live request. No upfront scanning of prompts, code, or configs.
- **Not a block-by-default system.** The default posture is "allow if no rule fires." Most actions clear the pipeline in tens of milliseconds; only those matching a real rule are denied.

## Next

- [Quickstart](quickstart.md) — credentials, your first authenticated call, your first signed audit row.
- [60-second tour](60-second-tour.md) — what the UI shows and where to click.
- [Why runtime governance](why-runtime-governance.md) — the threat model in depth and what static tooling cannot catch.
