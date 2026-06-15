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

- **Framework picker** — four tabs: EU AI Act, NIST AI RMF, SOC 2, and
  (added 2026-06-13 in A5) **India DPDP Act, 2023 + Rules (2025-11-13)**.
- **Period picker** — `period_start` and `period_end` dates. Required for every framework call.
- **"Generate" button** — fires the appropriate `getEuAiAct` / `getNist` / `getSoc2` / `getDpdp` call.
- **Evidence sections** — once loaded, the page shows:
  - **Identification** — tenant, period, generation timestamp.
  - **Controls** — checklist of controls relevant to the framework (e.g.
    for SOC 2: CC6.1 access control, CC7.2 monitoring, CC7.3 risk
    assessment; for DPDP: §8(5) reasonable security safeguards, §8(7)
    breach notification, §11 data principal rights, Rules Schedule II).
    Each control shows the supporting audit-row count and a sample of rows.
  - **AEVF back-reference** — each evidence row now carries
    `aevf_bundle_url`, `aevf_event_hash`, and `aevf_spec_version` so the
    auditor can click through to the verifiable bundle and run
    `aegis-verify` offline. See [AEVF Overview](../../AEVF/README.md).
  - **Retention assessment** (DPDP only) — the
    `meets_dpdp_minimum: true|false` flag plus the 365-day minimum vs the
    actual audit-window depth. Honest read: if the chain is shorter than
    365 days the bundle says so.
  - **Cryptographic chain proof** — the day's transparency root hash plus
    a chain-link to the previous day. Auditors can independently verify.
  - **Risk register** — the highest-risk findings observed in the period.
- **"Export PDF" button** — top right. Downloads a signed PDF embedding
  the same evidence.
- **"Export GRC bundle" button** (added 2026-06-14 in A6, live-verified on prod-ha the same day after the audit-service redeploy) — drops a CSV or
  JSON evidence file shaped for **Vanta / Drata / Secureframe / Hyperproof**
  ingestion. Each row is one `(audit_event, control_id)` pair across all
  four frameworks with the AEVF back-reference attached. Vanta correlates
  `control_id` against its control inventory and renders the verify link.

## Backend calls

| Action | HTTP | API path | Service | Live-verified 2026-06-14 |
|---|---|---|---|---|
| Generate EU AI Act report | GET | `/compliance/eu-ai-act?period_start=...&period_end=...` | api → audit | ✅ ~134 KB JSON |
| Generate NIST AI RMF report | GET | `/compliance/nist-ai-rmf?period_start=...&period_end=...` | api → audit | ✅ ~1.5 KB JSON |
| Generate SOC 2 report | GET | `/compliance/soc2?period_start=...&period_end=...` | api → audit | ✅ ~62 KB JSON |
| Generate DPDP report (A5) | GET | `/compliance/dpdp?period_start=...&period_end=...` | api → audit | ✅ 30/30 hits → 200, ~23 KB JSON, `report_type: "dpdp_bundle"` |
| Export PDF | GET (binary stream) | `/compliance/export?framework=...&period_start=...&period_end=...` | api → audit | ✅ |
| Export verifiable AEVF bundle | GET | `/compliance/verifiable-bundle/{framework}?period_start=...&period_end=...` (frameworks: `eu-ai-act`, `nist-ai-rmf`, `soc2`; DPDP not yet wired to the verifiable-bundle endpoint — use `/compliance/dpdp` above for the report) | api → audit | ✅ ~2.3 MB, V1–V6 PASS in `aegis-verify` |
| Export GRC bundle (A6) | GET | `/compliance/export/grc?format=json|csv&period_start=...&period_end=...` | api → audit | ✅ json: 20/20 → 200/206; csv: 20/20 → 200/206; multi-MB body |

> **Endpoint path forms — dashes, not underscores.** The verifiable-bundle and export endpoints accept `eu-ai-act`, `nist-ai-rmf`, `soc2`, `dpdp`, `grc`, `tool-ledger`. The underscore form `eu_ai_act` returns HTTP 400 `Unknown bundle_type`.

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
- [AEVF Overview](../../AEVF/README.md) — the open standard the bundles + GRC rows carry back-references to
- [Evidence Export Adapters](../../integrations/evidence-export.md) — SIEM + GRC + OTel channels for the same evidence
- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) — what the chain proof means
- [Key Rotation](../../operations/key-rotation.md) — how key rotation is reflected in the evidence
- [Threat Model](../../security/threat-scenarios.md) — what the framework controls map to

## Screenshot

![Compliance](../_screenshots/compliance.png)
