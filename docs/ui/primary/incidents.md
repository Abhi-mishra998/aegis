# Incidents

## What this page is for

The Incidents page is the SOC analyst's working surface. Each open incident is a record of "something happened that needs human review." Analysts triage, transition status, attach actions, export evidence as PDF for legal hold, and watch a side-by-side SOC feed of the live security events that may relate to the open incident set.

## Sidebar location & role gating

- **Sidebar group**: Primary nav.
- **Path**: `/incidents`.
- **Keyboard hint**: `G I`.
- **Minimum role for read**: `AUDITOR`.
- **Status transitions** (`PATCH /incidents/{id}`) and **action recording** (`POST /incidents/{id}/actions`) require `ADMIN` or `SECURITY`.
- **PDF export** is `AUDITOR`+; the audit row records the exporter.

## What you see

- **KPI tiles at the top** — Open incidents, High severity, Resolved this week, Average time-to-resolve. With trend arrows comparing to the previous week.
- **Incidents list** — paginated table. Columns: title, severity, status, assigned analyst, created timestamp.
- **Detail drawer** — opens when an incident is clicked. Shows the title, severity, status, the related audit_id (linking to Audit Trail), the action log (each `POST /incidents/{id}/actions` lands here), and the PDF Export button.
- **SOC Feed** — a separate column on the right. Streams the recent high-risk audit events for the tenant. Updates when the page polls.
- **Cross-agent correlation panel** — bottom. Shows agents with multiple incidents in the current window; clicking a cluster opens a filtered view.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load KPIs | GET | `/incidents/summary?agent_id=...` | api |
| List incidents (with filters) | GET | `/incidents?status=...&severity=...&limit=...&offset=...` | api |
| Get incident detail | GET | `/incidents/{id}` | api |
| Change status | PATCH | `/incidents/{id}` | api |
| Record an action | POST | `/incidents/{id}/actions` | api |
| Export to PDF | GET | `/incidents/{id}/export` | api → audit (PDF render) |
| Load valid status transitions (state machine) | GET | `/incidents/transitions` | api |
| Load SOC Feed | GET | `/audit/logs/soc-timeline?limit={n}` | audit |

## Auto-refresh & realtime

- **Whole-page refresh**: every 30 seconds via `setInterval(fetchAll, 30_000)` at `ui/src/pages/Incidents.jsx:467`.
- **No SSE.** Like the Audit Trail, the Incidents page is poll-based. The Live Feed page is the SSE surface; cross-link if you need sub-second updates.
- **State machine fetched once**: `incidentService.getTransitions()` runs at mount and is not re-fetched.

## Per-agent scoping

Yes. The KPI tiles and the incident list both take an optional `agent_id` parameter. When the sidebar picker is set, every fetch passes `selectedAgentId` so the page is scoped. The SOC Feed is scoped at the gateway by the JWT's tenant_id but does not filter per-agent.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No incidents match filters | "No incidents match current filters" | Clear filters via the Reset button. |
| SOC feed empty | `No security events in the selected window` | Extend the time range; the public demo has long quiet periods between traffic bursts. |
| Cross-agent correlation empty | `No cross-agent correlations in the current window.` | Expected for low-traffic tenants. |
| Action history empty on a fresh incident | Action log empty | Click "Record action" to log a triage step. |
| No incidents in fresh tenant (R4, 2026-06-13) | `This page lights up the moment a real deny lands. Run the Live Demo or one of the scripted scenarios to seed one.` | Click through to `/live-demo` (Primary nav, `G X`). The page provisions a critical-risk agent, runs a Groq-planned task, and the first time policy fires a deny it auto-opens an incident here. **Verified live on prod-ha 2026-06-14**: 3 R5 scenarios produced 4 denies → 6 deduplicated incidents in `/incidents` within ~6 s of the demo runs. The R5 scenario picker — `fintech_data_egress`, `devops_destruction`, `support_pii_exfil` — each emits at least one high-severity deny per run. |

## Auto-population pipeline (R4, fixed 2026-06-14)

Every gateway deny/escalate/kill publishes one event to the `acp:incidents:queue` Redis stream (gateway middleware `services/gateway/middleware.py:815-840`). The api service runs a durable consumer group (`api-incident-worker`) that reads from the stream, dedups by `sha256(tenant + agent + tool + trigger)` for 5 minutes, and writes signed Incident rows. If you trigger 4 denies on the same tool within the dedup window, you'll see 1 incident with violation-count bumped (see `services/api/main.py::_incident_consumer`).

**The `Trigger` field accepts both legacy short forms and the gateway's verbose forms.** The gateway publishes `trigger="policy_denied" | "escalation_required" | "agent_killed"`; the legacy schema also recognises `policy_deny | escalate | kill`. Both sets land as valid incidents (`services/api/schemas/incident.py:11`). A schema mismatch here was the root cause of the 2026-06-14 "incidents empty after demos" bug — pre-fix, every event failed Pydantic validation and stuck in the queue's pending list silently.

## Edge cases & known gotchas

- **403 on status change**: caller is `VIEWER` or `AUDITOR`. Re-login with a write role.
- **Status transition rejected**: the state machine forbids skipping states (e.g., you cannot jump from `open` directly to `resolved` without an `investigating` step). The API returns 400 with the allowed transitions.
- **PDF export 504**: the renderer is per-incident; very large incidents (lots of actions, many linked audit rows) can exceed the deadline. Retry once; if it persists, the PDF render queue is backed up and the audit service health page (Settings → System Health) will reflect it.
- **SOC feed and Incidents list disagree**: the SOC feed shows all recent high-risk events; the Incidents list shows only events that an operator (or auto-response rule) escalated into an incident. The disagreement is expected.
- **`selectedAgentId` URL deep link**: the page does not currently parse `?agent_id=...` from the URL; only the sidebar picker drives scoping. Direct-linked agent context is on the roadmap.
- **Same prompt run twice in 5 minutes appears as 1 incident**: that's the dedup window. Check the incident's `violation_count` for the actual hit count; it bumps on every duplicate suppressed.

## Related docs

- [API service](../../services/api.md) — owns the `incidents` table and the routes above
- [Audit service](../../services/audit.md) — source for the SOC feed and PDF render backend
- [Auto Response UI](../operations/auto-response.md) — the rule engine that opens many of these incidents automatically
- [Threat Scenarios](../../security/threat-scenarios.md) — the canonical attack cases that produce incidents

## Screenshot

![Incidents](../_screenshots/incidents.png)
