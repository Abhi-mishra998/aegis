# Security Policy

Aegis is a runtime security control plane. A vulnerability in Aegis is a
vulnerability in the control layer sitting in front of every downstream AI
agent tool call — take reports seriously and treat them privately until a
fix is available.

## Supported versions

Only the current `main` branch and the latest tagged release on PyPI receive
security fixes. Pin your SDK to the current published version and update
promptly when a security release is announced.

| Component | Supported |
|---|---|
| `main` branch on GitHub | ✅ |
| Latest tagged PyPI SDK (`aegis-anthropic`, `-openai`, `-langchain`, `-bedrock`, `-aevf`) | ✅ |
| Previous minor releases | best-effort until superseded |
| Anything older | ❌ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.**

Email **abhishekmishra09896@gmail.com** with:

- A clear description of the issue
- A minimal reproduction (script, curl invocation, or steps)
- The affected version / commit SHA / SDK version
- Whether the finding has been disclosed anywhere else

You should receive an acknowledgement within **72 hours**. If you don't,
resend the report — a mailbox may have filtered it. Reports are triaged in
order of severity, not order received.

## What we consider in scope

- Authentication or authorization bypass in the gateway
- Any path that lets an agent execute a tool the OPA policy or allow-list
  should have denied
- Kill-switch or autonomy-contract bypass
- Tampering with the audit chain that isn't caught by `acp verify-chain`
- Signature forgery on ed25519 receipts or transparency roots
- Cross-tenant reads that PostgreSQL RLS should have blocked
- Injection into the SDK / gateway proxy that reaches an upstream provider
  with unintended parameters

## What we consider out of scope

- Denial-of-service via non-authenticated request floods (mitigated at the
  WAFv2 layer in the reference deployment, and outside the design goals of
  the Aegis application layer)
- Findings in dependencies without a demonstrated exploit path into Aegis
- Missing rate limits on endpoints explicitly documented as unauthenticated
  (`/health`, `/status`, `/receipts/verify`)
- Social-engineering attacks against maintainers
- Reports from automated scanners without a reproducible exploit

## Disclosure timeline

Once we've confirmed the report:

1. **Day 0** — acknowledge receipt
2. **Day 1–7** — reproduce, assign severity, identify affected versions
3. **Day 7–30** — develop and test a fix
4. **Coordinated disclosure** — release the fix, publish an advisory, credit
   the reporter (unless they prefer anonymity)

## Cryptographic material

If you discover a compromise of any signing key or receipt-signing material,
please treat the report as **CRITICAL** and mark the email subject accordingly.
Key-compromise recovery procedures are documented internally in the
operations runbooks; the transparency-log chain design means historical
receipts remain verifiable against archived public roots even if the current
key must be rotated.

## Bug bounty

Aegis does not run a paid bug bounty at this time. We do offer public credit
in the CHANGELOG and, on request, a written acknowledgement suitable for
professional portfolios.
