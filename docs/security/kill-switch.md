# Kill Switch

*The single emergency lever in Aegis. One toggle, tenant-wide. Engaged in seconds, propagated in seconds, audited end-to-end, never silent.*

## What it does

When engaged for a tenant:

1. The gateway's stage 0 reads `acp:kill_switch:{tenant_id}` from Redis on every request.
2. If the key is present, every `/execute` returns HTTP 403 with body:

   ```json
   {
     "success": false,
     "error":   "kill_switch_engaged",
     "data": {
       "engaged_at": "<ISO-8601>",
       "engaged_by": "<user_id>",
       "reason":    "<string>"
     }
   }
   ```

3. The audit row is still written. The audit chain stays continuous.
4. Other stages of the pipeline are skipped — there is nothing to evaluate when the action is force-denied.

When disengaged, the key is deleted and subsequent requests proceed normally.

## Engagement contract

Source: `services/decision/router.py:79-115` (engage) and `:117-135` (disengage).

- `POST /decision/kill-switch/{tenant_id}` engages. Body requires a non-empty `reason`. Authorization: `ADMIN` or `SECURITY`.
- `DELETE /decision/kill-switch/{tenant_id}` disengages. Same authorization.
- `GET /decision/kill-switch/{tenant_id}` reads the state. Authorization: `AUDITOR`+.
- The `tenant_id` in the path must match the JWT's tenant. Cross-tenant kill-switching is rejected with 401.

Every engage and disengage produces an audit row `action="kill_switch_engaged"` or `"kill_switch_disengaged"` with the operator's `user_id`, the `reason`, and a timestamp. The audit chain makes the toggle non-repudiable.

## Propagation

The gateway reads `acp:kill_switch:{tenant_id}` on every request (no caching). Once the Redis key is set, every gateway worker observes the new state within one request lifetime. Under sustained traffic, the propagation lag is under 5 seconds tenant-wide.

The two EC2 hosts read from the same ElastiCache cluster, so propagation is uniform across the fleet.

## Fail-closed posture

If the gateway cannot read Redis at stage 0, the request is denied — kill switch is treated as engaged. This is enforced because a partial-availability Aegis is the same as no Aegis. A Redis outage is rare; a Redis outage during a real attack is rarer still; combining them produces the wrong answer if the fallback is "allow."

Source: `services/gateway/middleware.py` stage 0 dispatch.

## What it stops, what it doesn't

Stops:

- Every new `/execute` call for the tenant.
- Every agent token's ability to call `/execute` for this tenant (the route returns 403 before the agent role check).

Does not stop:

- **In-flight requests that passed stage 0 before the kill switch engaged.** The gateway does not retroactively cancel mid-pipeline requests. A request that reached stage 7 will complete normally.
- **Reads.** GET endpoints (audit, system health, etc.) continue to work so operators can investigate.
- **Other tenants.** The kill switch is per-tenant. Engaging it for tenant A has no effect on tenant B.
- **Internal background workers.** The audit outbox worker, the usage drain, the transparency root scheduler, the trust-score worker all keep running. They process state already in the system; they do not generate new tool executions.

## Engagement workflow

The runbook lives at [Kill Switch Engaged runbook](../operations/runbooks/kill-switch-engaged.md). The short version:

1. **Engage.** From the UI (Operations → Kill Switch → confirm modal with reason) or from the API (`POST /decision/kill-switch/{tenant_id}`).
2. **Notify.** The audit row triggers the standard Slack / PagerDuty webhook if configured. The Kill Switch UI shows the engagement timestamp and operator.
3. **Investigate.** Use Audit Trail, Forensics, and Identity Graph to identify the source of the incident.
4. **Remediate.** Common follow-ups: quarantine specific agents (Agents page), rotate credentials (Identity API), update policy rules (Policy Builder), tighten rate limits (Quota Management).
5. **Disengage.** When the investigation concludes and remediation is in place, `DELETE` the kill switch with a `reason` describing the resolution. The audit row records the disengagement.

## When to use it

Aegis's design philosophy: the kill switch is a last resort, not a first response. Most incidents are better handled by tighter scoping — quarantine the offending agent, tighten a policy rule, dial down a rate limit. The kill switch stops *everything* in the tenant, including legitimate traffic.

Good reasons to engage:

- Active credential theft with unknown attacker activity.
- Active prompt-injection campaign hitting multiple agents.
- Suspected misconfigured policy that is about to cause widespread damage.
- Customer-initiated emergency request ("turn it off, we're cutting over to our backup").

Reasons not to engage:

- One agent acting up. Quarantine the agent.
- One rule firing too often. Edit the rule.
- One user's account compromised. Revoke that user's tokens.

## Misuse risk and mitigation

The kill switch can be misused as a denial-of-service against the tenant itself. Mitigations:

- **Role gate**: only `ADMIN` and `SECURITY` can engage. `VIEWER` and `AUDITOR` cannot.
- **Reason required**: empty reason is rejected at the API. The audit row carries the reason.
- **Audit emission**: every engage and disengage is logged with the operator's identity. A pattern of unjustified engagements is detectable post-hoc.
- **Confirmation modal in UI**: not a one-click action.
- **Per-tenant scope**: a compromised `ADMIN` in tenant A cannot kill tenant B.

## Combined with other levers

The kill switch is the broadest lever. Two narrower levers exist for surgical response:

1. **Agent quarantine**. `PATCH /agents/{id} { status: "QUARANTINED" }`. Treats this one agent's permissions as DENY. Other agents continue normally.
2. **Token revocation**. `POST /auth/revoke` with a JTI. Stops that one token's ability to authenticate.

A typical incident response sequence:

- Detect (alert, Live Feed, customer report).
- **Triage** — is this one agent, one user, or platform-wide?
- **One agent** → quarantine the agent.
- **One user** → revoke the user's tokens, then change their role to VIEWER.
- **Platform-wide** → engage the kill switch.

## Combined with the audit chain

The kill switch is the most consequential primitive in Aegis; misuse should be detectable. Detection works through the audit chain:

- Every engage and disengage is a row signed and chained like any other audit row.
- The signed audit row makes the toggle non-repudiable.
- The daily Merkle root commits to the engagement event.
- A customer who archives the day's root can later prove an engagement happened (and when, and by whom) even if the platform's database is later wiped.

## Common questions

**Q: What if the operator who engaged is locked out before disengaging?**
A: Another `ADMIN` can disengage. The UI shows the engagement history so the new operator sees what was done.

**Q: What if Redis is down when the operator tries to engage?**
A: The Redis write fails; the engage API returns 503. The operator sees the error and can retry. The fail-closed posture means the platform behaves as if engaged when Redis is unreachable — so the tenant is already protected during the outage.

**Q: Can the kill switch be engaged automatically?**
A: Yes, via an Auto Response rule or a Playbook. The action type `engage_kill_switch` is built in. Same audit footprint as a manual engagement.

**Q: What does the SDK do when the kill switch is engaged?**
A: The SDK raises `KillSwitchEngagedError` on the next `/execute`. Applications should treat this as a hard stop and surface it to humans.

## Next

- [Decision service](../services/decision.md) — owns the kill switch primitive
- [Kill Switch UI](../ui/operations/kill-switch.md) — the human surface
- [Kill Switch Engaged runbook](../operations/runbooks/kill-switch-engaged.md) — what to do after engaging
- [Cryptographic Audit Chain](crypto-audit-chain.md) — the durability behind kill switch records
- [RBAC Roles](rbac-roles.md) — who can engage
