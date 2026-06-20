# Architecture Decision Records (ADRs)

Each significant architectural choice in Aegis is recorded here as a short
prose document. ADRs are *append-only*: when a decision is superseded, a new
ADR cites and supersedes the old one rather than editing it.

## Why ADRs

Enterprise procurement reviewers ask **why**, not just **what**. A 200-line
file in `services/` answers what; an ADR answers why we picked that approach
over the alternatives we considered. Without ADRs, the only durable answer
lives in commit messages and the founder's head — that's a bus-factor risk
and a procurement red flag.

## Conventions

- Filename: `ADR-NNN-short-kebab-title.md` where `NNN` is the next number.
- Status lifecycle: `Proposed → Accepted → (Superseded by ADR-XYZ)`.
- One decision per ADR. If a single sprint produced 3 decisions, write 3 ADRs.
- Length target: 1-2 screens. Cite source files at `path/file.py:line`.
- Use the template at `_template.md`.

## Index

| # | Title | Status | Date | Replaces / Superseded by |
|---|---|---|---|---|
| ADR-001 | [Cryptographic audit chain — DB trigger + Merkle + S3 mirror](ADR-001-audit-chain-design.md) | Accepted | 2026-06-20 | — |

## When to write an ADR

Write an ADR when:
- You picked one approach over a defensible alternative (Postgres vs. Kafka,
  ed25519 vs. RSA, OPA vs. inline rule code).
- You introduced a constraint that future contributors must respect
  (append-only audit, per-tenant key isolation, no client-trusted tenant_id).
- You closed a debate that recurs every quarter ("can we just use Mongo?").

You do *not* need an ADR for:
- A bug fix that doesn't change architecture.
- A refactor that's purely mechanical (rename, file split, dead-code removal).
- A feature whose design is entirely dictated by an external spec
  (e.g. implementing OAuth 2.0 PKCE — the RFC *is* the design).
