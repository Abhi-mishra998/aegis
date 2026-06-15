# Audit Trail

## What this page is for

The Audit Trail is the platform's primary investigation surface for the durable record of every decision Aegis has ever made. Each row is a signed audit log entry — the same row that downstream services verify, the same row that compliance auditors review. Operators come here to inspect a specific decision, verify the chain hasn't been tampered with, read the analyst notes attached to a row, and pull cryptographic receipts when required for external reporting.

## Sidebar location & role gating

- **Sidebar group**: Primary nav.
- **Path**: `/audit-logs`.
- **Keyboard hint**: `G A`.
- **Minimum role for read**: `AUDITOR`. `VIEWER` can also read the rows and verify the chain.
- **Adding a note** (`POST /audit/logs/{id}/notes`) requires `ADMIN` or `SECURITY`. A `VIEWER` sees the Notes panel but the submit button returns the platform's 403 on write attempts.

## What you see

- **Header bar** — page title, agent-scope chip (shows which agent the view is filtered to), and a "Verify chain" button.
- **KPI tiles** — Total decisions, Allow rate, Deny rate, Escalation rate. Computed from `/audit/logs/summary`.
- **Search panel** — collapsible. Filters by tool, decision, date range, finding, and free-text. Hitting Search calls `GET /audit/logs` with the filters as query params. (The page used to POST to `/audit/logs/search`; that path was blocked at the edge by AWS WAFv2's SQLi managed rule whenever the body contained `"limit":N`. The GET variant bypasses body inspection.)
- **Logs table** — the main grid. Each row: timestamp, tool, decision, agent, risk score, findings, and a "row id" hash badge.
- **Detail drawer** — opens to the right when you click any row. Tabs:
  - **Overview** — the full canonical row content.
  - **Receipt** — the signed payload, the prev_hash and event_hash, the key fingerprint.
  - **Explain** — a generated reasoning panel from `/audit/logs/{id}/explain`.
  - **Notes** — the Analyst Notes panel.
- **Analyst Notes** — a separate compact panel showing the note count for the selected row plus a quick-add form. Note types are `analysis`, `false_positive`, `confirmed_threat`, `escalated`.
- **Chain verification banner** — at the top, shows "Auto-Refresh: Chain ok" or, if a verification has failed, "Chain Broken — audit_chain_invalid".

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load KPIs | GET | `/audit/logs/summary` | audit |
| List logs (paginated) | GET | `/audit/logs?limit={n}&offset={n}[&agent_id=...]` | audit |
| Search logs | GET | `/audit/logs?agent_id=…&decision=…&tool=…&start_date=…&end_date=…&limit=…&offset=…` | audit |
| Verify chain integrity | GET | `/audit/logs/verify` | audit |
| Fetch a row's signed receipt | GET | `/audit/logs/{id}/receipt` | audit |
| Decision explanation | GET | `/audit/logs/{id}/explain` | audit |
| List notes on a row | GET | `/audit/logs/{id}/notes` | audit |
| Add a note | POST | `/audit/logs/{id}/notes` | audit |
| Export window to CSV | GET | `/audit/export` | audit |
| Export window to PDF | POST | `/audit/export` | audit |

## Auto-refresh & realtime

- **List refresh**: every 30 seconds when "Auto-refresh" is on (default: on). Const `AUTO_REFRESH_MS = 30_000` at `ui/src/pages/AuditLogs.jsx:304`.
- **Chain verification**: runs on every auto-refresh tick. The banner reflects the result of the last verification, not the live chain state, so a freshly-tampered row could take up to 30 seconds to surface.
- **No SSE** on this page. Live event arrival is the Live Feed page; this page is the durable record.

## Per-agent scoping

Yes. `effectiveAgentId = urlAgent || selectedAgentId || ''` at `ui/src/pages/AuditLogs.jsx:319`. URL parameter `?agent_id=...` takes precedence over the sidebar picker so a deep link from another page (e.g. the Agents profile) opens scoped. Cleared picker plus no URL parameter shows all agents in the tenant.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No rows match the current filters | `No audit records found` | Loosen filters or extend the date range. |
| Row has no notes yet | `No notes yet. Add one below.` | Type in the form; submit requires `ADMIN` or `SECURITY`. |
| Search returns "no logs" payload | Empty body with the same `No audit records found` message | Same as above. |
| Receipt detail unavailable (older row) | Receipt tab blank | Older rows predate the receipts feature; the row's decision is still authoritative. |

## Edge cases & known gotchas

- **Verify chain returns 200 with body `{valid: false}`**: this is not an HTTP error — the verifier returns 200 with the violation list inside the response. The banner flips to "Chain Broken" and surfaces the violation type. The previous bug where the API returned 409 (which the SDK threw as a hard error and the UI rendered as "internal server error") has been fixed.
- **Adding a note returns 500**: usually means the `audit_notes` table is missing on the target environment. The table is part of `services/audit/models.py::AuditNote`; run the migration. The public demo has been migrated.
- **Pagination off-by-one**: the API uses zero-based offset; the UI shows one-based page numbers in the footer.
- **Search payload too large**: the API caps `limit` at 1000. Larger windows should use the CSV/PDF export.
- **403 on POST note**: caller is `VIEWER` or `AUDITOR`. Re-login with `ADMIN` or `SECURITY`.
- **PDF export hangs**: very large windows can take 30+ seconds; the request streams a job id that the UI polls. Cancel and retry with a shorter window if the spinner does not advance.

## Related docs

- [Audit service](../../services/audit.md)
- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) — the prev_hash + ed25519 + Merkle root mechanics that this page surfaces
- [Audit Chain Violation runbook](../../operations/runbooks/audit-chain-violation.md) — what to do when verify reports a violation
- [Flow of a Decision](../../architecture/flow-of-a-decision.md) — how each row is produced

## Screenshot

![Audit Trail](../_screenshots/audit-trail.png)
