# Runbook: Kill Switch Engaged

## Alert

`KillSwitchEngaged` — fires informationally on every state change of `acp:kill_switch:{tenant_id}`. Not actionable on its own; the runbook is for the operator who decided to engage (or is investigating the engage event).

## Severity

**P1** by default. The platform is healthy; one tenant is intentionally halted.
Upgrade to **P0** if the engage was unintended (e.g., misconfigured Auto Response rule).

## Pre-engage checklist (before pressing the button)

If you are *considering* engaging the kill switch, work through this list first.

### Is the issue actually tenant-wide?

If only one agent is misbehaving, **quarantine the agent** instead. `PATCH /agents/{id} { status: "QUARANTINED" }`. Other agents continue normally.

If only one user's account is compromised, **revoke that user's tokens** instead. `POST /auth/revoke` with the JTI. Other users continue normally.

The kill switch stops everything. Use it only when narrower levers do not apply.

### Do you have the reason ready?

The current engage API records the reason server-side as the constant `manual_admin_lockdown` — operators communicate context via the audit-row note (`/audit/logs/{id}/notes`) and the Slack alert, not the engage payload itself. Have the human-readable reason ready for both. Be specific: "Active prompt injection campaign hitting CRM agent — investigating attacker IP 203.0.113.42" is better than "incident."

### Who needs to know?

The audit row produces a Slack notification if configured. If not, manually post to the incident channel before engaging.

## Engaging

### Via UI

1. Navigate to Operations → Kill Switch (visible only to `ADMIN` and `SECURITY` roles).
2. Click "Engage". A confirmation modal opens.
3. Enter the reason.
4. Confirm.

The status badge turns red within 5 seconds.

### Via API

```bash
curl -sS -X POST https://ha.aegisagent.in/decision/kill-switch/$TENANT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"action":"engage"}'
```

Expected: HTTP 200 with `{"success":true,"data":{"status":"engaged","tenant_id":"<uuid>"}}`. The audit chain records the engage event with the operator's `user_id` (extracted from the JWT) and the reason `manual_admin_lockdown`.

If you see HTTP 422 "Validation failed", you are running a pre-2026-06-01 decision-service image — the `path_tenant_id` dependency arg was unannotated and FastAPI treated it as a missing query param. Redeploy from the current tarball.

## Verify propagation

```bash
# Status should be engaged within 5 seconds
curl -sS https://ha.aegisagent.in/decision/kill-switch/$TENANT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq

# Verify a test execute is denied
curl -sS -X POST https://ha.aegisagent.in/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"db.query","payload":{"query":"SELECT 1"}}'

# Expected: 403 with error="kill_switch_engaged"
```

If a test execute returns 200, the propagation is incomplete. Wait 5 seconds; re-verify. If still 200, see "Common failure modes" below.

## Investigate

While the kill switch is engaged, investigate freely. Audit Trail, Forensics, and Identity Graph all continue to work — they are reads.

Typical investigation steps:

1. **Audit Trail** → filter to recent high-risk events. What was the attack vector?
2. **Forensics** → run blast-radius on any compromised agent. What was reached?
3. **Identity Graph** → are other tenants in scope? Compromise simulation.
4. **Live Feed** → does in-flight traffic still match the attack signature?

## Remediate

Before disengaging:

- **Quarantine the offending agent.** Even after disengaging the kill switch, the agent should not return to active duty until the root cause is fixed.
- **Update the policy.** If the attack passed stage 4 (policy), add a deny rule via Policy Builder. Test in simulation, then activate.
- **Rotate credentials.** If a token was stolen, rotate it via `POST /auth/revoke` and the SSO / password reset flow.
- **Tighten rate limits.** If the attack used burst traffic, lower the tenant's `requests_per_second` or `daily_inference_cost_cap_usd` cap.
- **Notify the tenant's contact.** They should know their service was paused and the reason.

## Disengage

### Via UI

1. Navigate to Operations → Kill Switch.
2. Click "Disengage". A confirmation modal opens.
3. Enter the reason (typically describes the resolution).
4. Confirm.

### Via API

```bash
curl -sS -X DELETE https://ha.aegisagent.in/decision/kill-switch/$TENANT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID"
```

The DELETE route takes no body. The audit row records the disengage event with the operator's JWT-derived `user_id`. The POST route also accepts `{"action":"disengage"}` for callers that prefer one HTTP verb.

## Verify recovery

```bash
# Status should be disengaged
curl -sS https://ha.aegisagent.in/decision/kill-switch/$TENANT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" | jq '.data.status'  # should be "disengaged"

# A test execute should now succeed
curl -sS -X POST https://ha.aegisagent.in/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT_ID" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"db.query","payload":{"query":"SELECT 1"}}'

# Expected: 200 with the decision envelope
```

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Engage 503 | Redis unreachable | Verify ElastiCache is up; the gateway fails closed during Redis outages so the platform is already protected |
| Engage 400 "reason required" | Empty reason in payload | Provide a non-empty reason |
| Engage 401 cross-tenant | path `tenant_id` mismatches JWT | Use a token for the tenant you intend to halt |
| Engage propagated to only one EC2 | Highly unlikely (shared ElastiCache); if it happens, restart the gateway on the unaffected EC2 | `docker compose restart gateway` on the EC2 still allowing traffic |
| Disengage attempted by `VIEWER` | Role gate | Re-login as `ADMIN` or `SECURITY` |
| Quarantined agent still hitting `/execute` after disengage | Quarantine and kill switch are independent | The quarantine stays; lift via `PATCH /agents/{id} { status: "ACTIVE" }` separately |

## Post-incident

1. File an incident report:
   - Engage timestamp + operator + reason.
   - Disengage timestamp + operator + reason.
   - Root cause of the incident that triggered the engage.
   - Remediations applied.
   - Customer-facing notification status.
2. Update Auto Response rules if the same pattern should self-handle next time.
3. Review the kill-switch activation history (`GET /audit/logs?action=kill_switch_engaged`) to identify trends.

## Related code

- `services/decision/router.py::kill_switch_set` and `kill_switch_disengage`
- `services/gateway/middleware.py` — the stage 0 check
- `services/gateway/_mw_audit.py` — the audit emission

## Next

- [Kill Switch security](../../security/kill-switch.md) — the deep dive
- [Decision service](../../services/decision.md) — the owner
- [Audit service](../../services/audit.md) — the recorder
