# Playbooks

## What this page is for

The Playbooks page is where pre-canned remediation workflows are authored, triggered manually, and reviewed after running. A playbook is a directed sequence of steps — `notify_slack`, `quarantine_agent`, `engage_kill_switch`, `rotate_credentials`, `freeze_billing` — bundled under a name with optional auto-trigger rules. When the audit chain produces a finding that matches a rule, the playbook runs (manually-triggered or auto-triggered); each step is recorded for later review.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown (also linked from Settings → Developer).
- **Path**: `/playbooks`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR`.
- **Create, update, delete, manually trigger** require `ADMIN` or `SECURITY`. Step idempotency keys protect against accidental double-triggers.

## What you see

- **Playbooks list** — left column. Each row: name, mode (`active` / `simulate` / `disabled`), trigger conditions summary, last run timestamp, run count.
- **Templates panel** — top right. The 4 built-in templates: `incident_response_basic`, `prompt_injection_response`, `cost_anomaly_response`, `chain_violation_response`. Clicking a template clones it into the tenant.
- **Detail panel** — bottom right when a playbook is selected. Shows the step sequence and the "Trigger now" button.
- **Run history** — collapsible panel under detail. Each past run with status, started_at, completed_at, per-step results.
- **Auto-trigger stats** — top of detail panel. How many times the auto-trigger fired and the most recent timestamp. Backed by `/playbooks/autotrigger-stats`.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List playbooks | GET | `/playbooks` | autonomy |
| List templates | GET | `/playbooks/templates` | autonomy |
| Aggregate stats | GET | `/playbooks/stats` | autonomy |
| Auto-trigger stats | GET | `/playbooks/autotrigger-stats` | autonomy |
| Clone a template into a playbook | POST | `/playbooks` (with template_id in body) | autonomy |
| Update a playbook (toggle active, edit) | PATCH | `/playbooks/{id}` | autonomy |
| Soft-delete | DELETE | `/playbooks/{id}` | autonomy |
| Trigger manually | POST | `/playbooks/{id}/trigger` | autonomy |
| Past runs for a playbook | GET | `/playbooks/{id}/runs` | autonomy |

## Auto-refresh & realtime

- **No setInterval on this page.** The list reloads on actions but does not poll.
- **Manual refresh button** at the top of the list re-fetches all panels.
- **No SSE.** A long-running playbook updates its row at the next manual refresh.

## Per-agent scoping

No. Playbooks are tenant-scoped definitions. A single run may target a specific agent via its context payload (`{"agent_id":"..."}`).

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No playbooks installed | `No playbooks installed` | Click a template card to clone it. |
| No templates returned | `No templates available.` | Confirm the autonomy service is healthy — the templates are platform-shipped. |
| Selected playbook has no past runs | `No runs yet.` | Click "Trigger now" or wait for an auto-trigger match. |

The public production demo currently has 0 playbooks created — the 4 templates are visible but no instance has been cloned from them.

## Edge cases & known gotchas

- **`/playbooks/autotrigger-stats` returns 422 with "uuid parse"**: a route-order regression. The static path must be declared before the parameterized `/playbooks/{playbook_id}` route in `services/autonomy/router.py`. The fix landed; if it appears again, file an issue.
- **Trigger button returns 403**: caller is `VIEWER` or `AUDITOR`. Trigger requires write role.
- **A step in a manually-triggered run fails**: the `playbook_runs.step_results` records the failure; subsequent steps are skipped. Re-trigger with the same `idempotency_key` to re-run only the failed step.
- **Auto-trigger not firing for an obvious match**: the rule may be `mode="simulate"` instead of `"active"`. Toggle in the editor.
- **Cross-EC2 deploy**: the auto-trigger watcher runs in each `acp_autonomy` container. Both EC2s independently consume the audit stream; the playbook-run idempotency key prevents double execution.
- **Soft delete is forensic-friendly**: deleting a playbook sets `deleted_at`; run history stays intact.

## Related docs

- [Autonomy service](../../services/autonomy.md) — owns the table and the runner
- [Auto Response UI](auto-response.md) — sibling page for rule-based automation
- [Audit service](../../services/audit.md) — source of auto-trigger matches

## Screenshot

![Playbooks](../_screenshots/playbooks.png)
