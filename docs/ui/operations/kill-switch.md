# Kill Switch

## What this page is for

The Kill Switch page is the single emergency lever in Aegis. One toggle, tenant-wide. When engaged, every gateway worker in the tenant sees the new state within 5 seconds and rejects every subsequent `/execute` call with a structured 403. The page is the operational version of the runbook everyone hopes to never need.

The page also shows the recent activation history so an operator can answer "has this been engaged before, when, and by whom."

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown — but visible only when the user has `canViewKillSwitch` (`ADMIN` or `SECURITY`). A `VIEWER` or `AUDITOR` does not see the menu entry.
- **Path**: `/kill-switch`.
- **Keyboard hint**: none.
- **Engage and disengage** require `ADMIN` or `SECURITY`. The non-empty `reason` field is required by the API to make every toggle non-repudiable.

## What you see

- **Status badge** — large, top-of-page. Green "Disengaged" or red "Engaged at \<timestamp\> by \<operator\>".
- **Engage button** — only enabled when status is Disengaged. Opens a confirmation modal with a reason field; the API rejects empty reasons.
- **Disengage button** — only enabled when status is Engaged. Same confirmation pattern.
- **Activation history** — bottom panel. Last 20 audit rows where the kill switch was toggled. Each row shows the operator, the action (engage / disengage), the reason, and the timestamp.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Get current status for the tenant | GET | `/decision/kill-switch/{tenant_id}` | decision |
| Engage | POST | `/decision/kill-switch/{tenant_id}` | decision |
| Disengage | DELETE | `/decision/kill-switch/{tenant_id}` | decision |
| Activation history | GET | `/audit/logs?action=kill_switch_engaged&limit=20` (called as `auditService.getKillSwitchHistory`) | audit |

## Auto-refresh & realtime

- **Status poll**: every 30 seconds via `setInterval(fetchStatus, 30_000)` at `ui/src/pages/KillSwitch.jsx:70`.
- **No SSE.** The status badge updates at the next poll; a confirmation modal force-refreshes on action.

## Per-agent scoping

No. The kill switch is intrinsically tenant-wide. There is no per-agent kill switch — quarantine on the Agents page is the per-agent analog.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Tenant has never engaged the kill switch | `No kill-switch activations recorded.` | Healthy. The audit chain will record the first engagement when it happens. |

## Edge cases & known gotchas

- **5-second propagation lag**: the gateway workers poll their kill-switch flag periodically. After engaging, the first 1–5 seconds may still allow requests. For an immediate hard stop combined with a kill switch, also pause traffic at the load balancer.
- **403 on engage / disengage**: caller is `VIEWER` or `AUDITOR`. Re-login with `ADMIN` or `SECURITY`.
- **Reason field empty**: the API rejects with 400. The UI prevents this client-side but a direct API call without `reason` will be denied.
- **Engaged but agents still calling `/execute` for several seconds**: expected per the propagation lag above. The audit row records both the engage event and any in-flight executions; in-flight requests that already passed stage 0 will complete normally (they do not retroactively re-check the kill switch mid-pipeline).
- **Disengage doesn't re-enable a quarantined agent**: kill switch and quarantine are independent. A quarantined agent stays quarantined after the kill switch lifts.
- **Cross-tenant engage attempt**: the handler verifies the path param `tenant_id` matches the JWT's tenant. A cross-tenant engage is impossible from the API.
- **Per-EC2 flap**: both EC2s read the same Redis key on the same ElastiCache cluster. Once engaged, both observe the new state at the same poll cadence.

## Related docs

- [Decision service](../../services/decision.md) — owns the kill switch state and the toggle endpoints
- [Gateway service](../../services/gateway.md) — checks the flag at stage 0 of every request
- [Kill Switch security](../../security/kill-switch.md) — the deep dive and threat model
- [Kill Switch Engaged runbook](../../operations/runbooks/kill-switch-engaged.md) — what to do after engaging

## Screenshot

![Kill Switch](../_screenshots/kill-switch.png)
