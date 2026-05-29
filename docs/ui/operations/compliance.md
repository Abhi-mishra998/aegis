# Compliance

## What this page is for

The Compliance page produces evidence reports against the three frameworks Aegis ships with: EU AI Act, NIST AI RMF, and SOC 2. Each report draws its data from the signed audit chain plus the per-tenant configuration (RBAC, kill switch usage, key rotation history). The output is a structured payload viewable on-screen and exportable as a PDF for submission to auditors.

This is the page Compliance teams open when an audit window arrives.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/compliance`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **PDF export** is also `AUDITOR`+; the export action produces an audit row recording who pulled the evidence.

## What you see

- **Framework picker** — three tabs: EU AI Act, NIST AI RMF, SOC 2.
- **Period picker** — `period_start` and `period_end` dates. Required for every framework call.
- **"Generate" button** — fires the appropriate `getEuAiAct` / `getNist` / `getSoc2` call.
- **Evidence sections** — once loaded, the page shows:
  - **Identification** — tenant, period, generation timestamp.
  - **Controls** — checklist of controls relevant to the framework (e.g. for SOC 2: CC6.1 access control, CC7.2 monitoring, CC7.3 risk assessment). Each control shows the supporting audit-row count and a sample of rows.
  - **Cryptographic chain proof** — the day's transparency root hash plus a chain-link to the previous day. Auditors can independently verify.
  - **Risk register** — the highest-risk findings observed in the period.
- **"Export PDF" button** — top right. Downloads a signed PDF embedding the same evidence.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Generate EU AI Act report | GET | `/compliance/eu-ai-act?period_start=...&period_end=...` | api → audit |
| Generate NIST AI RMF report | GET | `/compliance/nist-ai-rmf?period_start=...&period_end=...` | api → audit |
| Generate SOC 2 report | GET | `/compliance/soc2?period_start=...&period_end=...` | api → audit |
| Export PDF | GET (binary stream) | `/compliance/export?framework=...&period_start=...&period_end=...` | api → audit |

## Auto-refresh & realtime

- **No auto-refresh.** Reports are generated on demand.
- **No SSE.**

Generation is a heavyweight aggregate over the audit chain for the period; auto-refresh would hammer the database.

## Per-agent scoping

No. Compliance reports are tenant-scoped by definition. An agent filter would produce evidence for one agent in isolation, which is not what auditors are asking for.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Period not selected | `No evidence loaded.` | Pick a `period_start` and `period_end` and click Generate. |
| Period has no audit data | Report still renders, with `audit_row_count: 0` and empty sample arrays | Use a wider period that includes recent activity. |

## Edge cases & known gotchas

- **422 on Generate**: missing `period_start` or `period_end`. The framework call requires both. The UI's date picker initializes them but a direct API call without query params returns 422.
- **PDF download fails with 502**: PDF generation on a very large period (e.g. full year) times out at the gateway's 60-second proxy deadline. Use shorter periods or split the year into quarters.
- **Numbers disagree between viewer and PDF**: the viewer renders against the latest audit data; the PDF is rendered at export time. A small delta (within 30 seconds) can appear if traffic continues during the export. The PDF is the durable artifact.
- **Different frameworks share the same audit rows**: each report applies a different lens to the same chain. A row that appears under SOC 2 CC7.2 (monitoring) may also appear under NIST AI RMF MAP.5.1 (incident response). This is correct, not duplicated.
- **Per-EC2 flap**: `/compliance/*` proxied via the strict-prefix nginx rule for `compliance/`; stable.

## Related docs

- [Audit service](../../services/audit.md) — produces the evidence
- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) — what the chain proof means
- [Key Rotation](../../operations/key-rotation.md) — how key rotation is reflected in the evidence
- [Threat Model](../../security/threat-scenarios.md) — what the framework controls map to

## Screenshot

![Compliance](../_screenshots/compliance.png)
