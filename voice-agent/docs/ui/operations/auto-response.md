# Auto Response

## What this page is for

Auto Response is the rule-based automation surface. Where Playbooks defines a sequence of remediation steps, Auto Response defines the *condition* under which a playbook (or an inline action) fires. The page covers rule authoring, simulation against historical events, version history and rollback, feedback recording (to tune over time), pending-approval queue, latency dashboards, and the metrics that drive operator tuning.

It is the most feature-dense page in the platform.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/auto-response`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Create, update, delete, simulate, rollback, approve, feedback** all require `ADMIN` or `SECURITY`.
- The master **toggle** (enable or disable Auto Response globally for the tenant) is `ADMIN` only.

## What you see

- **Master toggle** — top of the page. Single switch turning the whole AR engine on or off for the tenant.
- **Rules panel** — list of active and disabled rules with mode badges (`active` / `simulate` / `disabled`).
- **Rule editor** — opens when a rule is clicked. Sections for match condition (finding, agent pattern, tool, severity), actions (inline or linked playbook), and the `approval_required` flag.
- **Simulate panel** — runs the rule against historical events for the chosen time range. Shows which events would have triggered.
- **Version history** — under detail. Each row is a past version; "Rollback" reverts to that revision.
- **Feedback recorder** — per-rule. Records "this was a false positive" or "this should suppress for N minutes" feedback for tuning.
- **Pending approvals queue** — separate panel. When a rule has `approval_required=true`, matches create entries here for a human to approve or reject.
- **Metrics tiles** — coverage, hit rate, p95 latency, last-run timestamp.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List rules | GET | `/auto-response/rules` | api |
| Create rule | POST | `/auto-response/rules` | api |
| Read / update / delete rule | GET / PATCH / DELETE | `/auto-response/rules/{id}` | api |
| Simulate a rule | POST | `/auto-response/simulate` | api |
| Version history | GET | `/auto-response/rules/{id}/history` | api |
| Rollback to a version | POST | `/auto-response/rules/{id}/rollback/{version}` | api |
| Feedback on a rule | POST | `/auto-response/rules/{id}/feedback` | api |
| Master toggle status | GET | `/auto-response/toggle` | api |
| Toggle on / off | POST | `/auto-response/toggle` | api |
| Aggregate metrics | GET | `/auto-response/metrics` | api |
| Pending approvals | GET | `/auto-response/pending` | api |
| Approve / reject pending | POST | `/auto-response/pending/{key}/approve` | api |
| Latency stats | GET | `/auto-response/latency` | api |
| Linked playbook runs | GET | `/playbooks/{id}/runs` | autonomy |

## Auto-refresh & realtime

- **Whole page refresh**: every 30 seconds via `setInterval(fetchAll, 30_000)` at `ui/src/pages/AutoResponse.jsx:1015`.
- **No SSE.** Pending approvals appear at the next poll.

## Per-agent scoping

Optional. Rules can scope to specific agents via the match condition's `agent_pattern`. The page itself shows the full rule set for the tenant; the sidebar agent picker does not filter the list.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No incidents in simulation window | `No incidents in the selected window.` | Extend the window. |
| Rule has no version history | `No version history yet.` | The rule has not been edited since creation. |
| No pending approvals | `No pending approvals` | Healthy — no `approval_required` matches are waiting. |
| Selected playbook has no runs | `No runs yet.` | The rule has not fired yet. |
| No playbooks at all | `No playbooks installed` | Go to Playbooks → install a template. |

The public production demo currently has 0 auto-response rules configured.

## Edge cases & known gotchas

- **Simulation reports many "would have fired" but production is quiet**: the rule is in `simulate` mode. Switch to `active` to enforce.
- **Approval pending never expires**: the TTL on `acp:ar_pending:{key}` may not be set; check the row's `expires_at`. Manual cleanup via the rejecter UI is the workaround.
- **Rollback skips a version**: rollback walks the history; if a version is missing (e.g. deleted by an admin), the rollback returns 400. Re-author the rule.
- **Feedback does not change behavior immediately**: feedback is *recorded* for future model tuning; it does not directly suppress matches. The `suppress_min` field on feedback adds a per-rule throttle for the specified minutes.
- **Latency tile shows N/A**: no recent matches. Tile populates once the rule fires at least once.
- **Master toggle off, but rules still fire**: an inconsistency would indicate Redis state out of sync; force-set `acp:ar_toggle:{tenant_id}` to the desired value.

## Related docs

- [API service](../../services/api.md) — owns the AR engine and tables
- [Autonomy service](../../services/autonomy.md) — owns the playbooks AR rules invoke
- [Playbooks UI](playbooks.md) — sibling page
- [Audit service](../../services/audit.md) — source of events AR matches

## Screenshot

![Auto Response](../_screenshots/auto-response.png)
