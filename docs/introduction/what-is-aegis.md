# What is Aegis?

**Aegis is a runtime governance and security control plane for AI agents.**

Every agent action — executing SQL, invoking an API, modifying cloud infrastructure, accessing customer data — is evaluated before it reaches the target system. Aegis applies authentication, policy controls, behavioral analysis, rate limits, autonomy constraints, and per-tenant governance rules in real time. Actions that violate policy are blocked at the gateway. Allowed actions pass through with a p95 evaluation latency of about 21 ms in the current production deployment.

Every decision — allowed or denied — is recorded in a tamper-evident cryptographic audit chain. Each row is signed (ed25519), linked to the previous row by hash, and rolled into a daily Merkle transparency root. This gives security, compliance, and forensic teams a verifiable history of agent behavior that cannot be silently modified after the fact.

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

## Production deployment

- **2× EC2** behind an Application Load Balancer at `aegisagent.in`
- **24 containers** per host: 12 application services plus Postgres, Redis, OPA, PgBouncer, Prometheus, Grafana, Jaeger, Alertmanager, and supporting workers
- **Postgres (RDS)** for application state, **Redis (ElastiCache)** for caches and Pub/Sub, **S3** for receipts and tenant exports

## What's measured in production

- **p95 gateway evaluation latency**: ~21 ms
- **Kill switch propagation**: < 5 seconds, tenant-wide
- **Attack-scenario block rate**: 100% on the four shipped test cases (PII exfiltration via bulk CRM export, RCE via `rm -rf`, SQL injection via `DROP TABLE`, k8s production namespace deletion)
- **Audit chain integrity violations**: 0 in the live chain (5,409+ decisions)
- **Test suite**: 2,044+ tests

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
