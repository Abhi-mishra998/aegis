# Scheduled Reports

## What this page is for

Schedule recurring PDF reports to land in stakeholders' inboxes without an operator pulling them by hand. Compliance teams configure monthly board reports; SOC teams configure weekly incident summaries; finance configures quarterly billing reports.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/scheduled-reports`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Create, update, delete, run-now** require `ADMIN`.

## What you see

- **Reports table** — name, frequency (`weekly` / `monthly` / `quarterly`), framework (audit / EU AI Act / billing), recipients, next-run timestamp, last-run status.
- **"New report" form** — name, framework, frequency, recipient list (comma-separated), period offset.
- **Run-now button per row** — triggers an immediate run for testing.
- **Delivery history panel** — past runs with status and delivery timestamps.
- **Active toggle per row** — pause without deleting.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List reports | GET | `/reports/scheduled` | api |
| Create report | POST | `/reports/scheduled` | api |
| Update report | PATCH | `/reports/scheduled/{id}` | api |
| Delete report | DELETE | `/reports/scheduled/{id}` | api |
| Run now | POST | `/reports/scheduled/{id}/run` | api |
| Delivery history | GET | `/reports/scheduled/{id}/history?limit=10` | api |

## Auto-refresh & realtime

- **No auto-refresh.** The list reloads after actions.

## Per-agent scoping

No. Reports are tenant-level.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No reports configured | `No scheduled reports` | Click "New report". |
| Report has no past runs | `No deliveries recorded yet.` | The first scheduled run hasn't happened yet. |

## Edge cases & known gotchas

- **Recipient list parsing**: emails are comma-separated; whitespace is trimmed. Per-recipient delivery uses the platform's webhook layer.
- **Run-now does not block on delivery**: returns the queued status; check delivery history a minute later.
- **Stale "last-run failed" badge**: delivery worker may have failed on a transient SMTP issue. Re-run with the Run-now button.
- **Per-EC2 flap**: `/reports/*` proxies via the strict-prefix `reports/` rule.

## Related docs

- [API service](../../services/api.md)
- [Audit service](../../services/audit.md) — produces the PDF content
- [Compliance UI](../operations/compliance.md)

## Screenshot

![Scheduled Reports](../_screenshots/scheduled-reports.png)
