# Security Policy

## Reporting a vulnerability

If you find a security vulnerability in Aegis, **do not open a public issue.**

Email: `abhishekmishra09896@gmail.com`
Subject line: `[SECURITY] Aegis vulnerability report`

Please include:

- A clear description of the vulnerability
- Steps to reproduce (the simpler, the better)
- The component affected (gateway, audit chain, OPA policy engine, SDK, etc.)
- Potential impact — what can an attacker do with this?
- A suggested fix if you have one

I'll acknowledge within 48 hours and work with you on a fix and disclosure timeline before anything goes public.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x (current) | ✅ Active |

## Disclosure policy

I follow responsible disclosure:

1. You report the vulnerability privately.
2. I reproduce it and assess severity.
3. I develop and test a fix.
4. Fix ships in a tagged release.
5. I publish a GitHub Security Advisory with full details.
6. You get credited (unless you prefer to stay anonymous).

Typical timeline: 7–14 days from report to disclosure for non-critical issues, 48–72 hours for critical ones.

## Scope

In scope:

- Authentication and authorization bypass (JWT validation, revocation, allow-list enforcement)
- Audit chain integrity violations (forged receipts, chain gaps, Merkle root manipulation)
- OPA policy bypass (accessing tools that should be hard-denied)
- Cross-tenant data access
- Kill switch failure (agent continues operating after kill switch is engaged)
- SDK vulnerabilities that expose user credentials or bypass policy checks

Out of scope:

- Issues in third-party dependencies (report those upstream)
- Denial of service against a local development instance
- Social engineering

## Security design

For an overview of Aegis's security model, see:

- [10-Layer Security Architecture](README.md#%EF%B8%8F-10-layer-security-architecture) in the README
- [Cryptographic Trust Chain](README.md#-cryptographic-trust-chain) — ed25519 receipts, HMAC chain, Merkle roots
- [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) — STRIDE analysis per service
