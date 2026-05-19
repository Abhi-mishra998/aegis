# Changelog

All notable changes to Aegis are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] — 2026-05-19

### Added

- **Cryptographic transparency log** — daily Merkle root with `prev_root_hash` chaining. Roots form an append-only chain; historical roots can be verified offline with `acp verify-root`
- **Visual Policy Builder** — build OPA Rego rules via UI without writing Rego. JSON preview + dry-run simulation against last 24h traffic
- **Agent Playground** — execute tool calls against the live decision engine and inspect risk signal breakdown in real time
- **Attack Simulation panel** — 7 pre-built scenarios (SQL injection, file deletion, credential harvesting, mass exfiltration, network scan) run against live gateway and log to audit chain
- **ARE (Auto-Response Engine) rule builder** — IF/THEN UI for defining automated responses (KILL, ISOLATE, THROTTLE, ALERT) with cooldown and rate-cap controls
- **Real-Time Observability page** — live decision feed, AI threat intelligence narratives via Groq, 5-signal risk breakdown panel
- **Security Operations Center** — SOC-style overview: request totals, threat counts, risk distribution heatmap, top threat agents leaderboard
- **Behavioral Forensics page** — click-to-drill incident reconstruction with full forensic timeline per agent
- **RBAC Manager** — four built-in roles (ADMIN, SECURITY_OFFICER, ANALYST, VIEWER) with agent-scoped assignments
- **Identity Graph blast-radius simulation** — BFS at configurable depth from any compromised node, quantified blast radius output
- **Flight Recorder** — step-by-step gateway pipeline replay with pre/post-gate snapshots and 2/5/15/60-min windows
- **Autonomy Contracts** — explicit action budgets (max_cost, max_runtime, max_destructive_ops_per_hour) per agent with real-time violation feed
- **Emergency Kill Switch** — tenant-wide isolation persisted to both Redis and Postgres; survives Redis FLUSHDB; toggle event cryptographically signed
- **Transactional outbox** — audit row and billing event written in same DB transaction; 100% delivery guarantee
- **Per-tenant quota enforcement** — three-layer limit: token bucket (RPS + burst) + daily cap + monthly cap with 80% warning
- **Offline chain verifier** — `acp verify-chain`, `acp verify-root`, `acp verify-receipt` — pure-function, zero trust in running system
- **Slack auto-notifications** — structured critical incident alerts with incident ID, severity, agent, tool, violations
- **Jaeger distributed tracing** — end-to-end DAG trace topology across all 12 services
- **Three end-to-end demo packs** — DevOps Agent (K8s), DB Copilot (SQL analyst), Support Agent (customer service)
- **Python SDK** — `@acp.protect` decorator for 5-line integration; `acp.guard()` for framework-agnostic use; `verify_receipt()` offline verifier
- **~330 pytest tests** — unit, integration, and E2E coverage

### Architecture

- 12 FastAPI microservices across 25 Docker containers
- Single entry point (gateway `:8000`) with 5 sequential fail-closed gates
- P95 decision latency: **27ms** on full pipeline under 100 concurrent users
- Attack block rate: **100%** (346/346 attack-pattern requests)
- Billing accuracy: **100%** reconciled via transactional outbox

---

## [0.1.0] — 2026-04-21

### Added

- Initial implementation of the ACP runtime security control plane
- JWT issuance, validation, and revocation
- OPA policy engine integration with Rego hard-deny rules
- Per-tool allow-list enforcement
- ed25519-signed audit receipts
- HMAC-chained audit log (16 parallel shards)
- Basic behavioral risk scoring
- React 18 SPA with SOC visibility dashboards
- Docker Compose stack (25 containers)
- PostgreSQL + Redis + OPA Bundle Server infrastructure
