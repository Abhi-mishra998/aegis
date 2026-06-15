# Sprint 6 — Auto-Remediation Framework

**Status:** in_progress
**Closes debt:** TD-4 — detection without remediation. `quarantine_agent()`
sets a Redis flag and that's it. Nothing revokes the API key, nothing kills
active JWTs from the policy path, nothing pages on-call.
**Depends on:** Sprint 4 (the storyline is the unit remediation fires per).
**Blocks:** —

---

## Why this matters

Today Aegis is loud: when a kill chain crosses the deny line we write an
audit log, set `acp:quarantine:{tid}:{aid}`, and return a 403 to the agent.
The next /execute call from the *same* agent — same API key, same JWT —
sails through the gateway just fine, because:

- The quarantine flag is checked by the policy path but not by JWT auth.
- The JWT in the attacker's hand still passes signature validation.
- No webhook went out, so the on-call team learns from the dashboard at
  morning standup, not at 02:47 when it happened.

A real breach reads: "Aegis caught it at 02:47, attacker reused the same
key at 02:48, attacker reused it at 02:49, attacker eventually
succeeded at 03:14 because the Redis flag had expired." Detection
without remediation is half a product.

Sprint 6 is the other half. When an incident transitions to
`quarantined`, Aegis:

1. Adds the agent to the per-tenant revoked-agents set so subsequent
   JWTs minted for the same `(tenant, agent)` pair are rejected at
   auth.
2. Publishes a token-revocation event on `acp:token:revocations` so
   every gateway worker drops the currently-in-flight token from its
   local LRU cache.
3. Optionally fires the tenant's configured webhook with the storyline
   summary so PagerDuty / Slack / Opsgenie can wake the right human.
4. Writes a per-action ledger row to Redis so the SOC can audit "what
   did Aegis do for me" without grepping the gateway logs.

## Goal

A `RemediationExecutor.execute(incident_id)` call that:

- Reads the tenant's `RemediationPolicy` (which actions are enabled).
- Fires the enabled actions in parallel.
- Persists a `RemediationAction` row per action with status `done` or
  `failed` and a human-readable result string.
- Is idempotent — re-running for the same incident doesn't double-fire
  the actions (the executor checks the ledger first).

Wired into the recorder so a storyline transitioning to `quarantined`
triggers the executor automatically. Also exposed via a router so the
SOC can re-fire if a webhook initially failed.

## Algorithm

### Action types

- **`revoke_api_key`** — `SADD acp:remediation:revoked_agents:{tid} {aid}` +
  `EXPIRE 86400`. The auth middleware checks this set on every request
  (a 1-call Redis check; cheap). A revoked agent gets 401 + 
  `error: agent_revoked_by_remediation` on every subsequent request.
- **`kill_active_tokens`** — Publish `{"tenant_id": tid, "agent_id": aid,
  "all_for_agent": true}` to `acp:token:revocations`. The existing gateway
  listener (see `services/gateway/auth.py:147`) drops any cached token
  whose claims match.
- **`page_oncall`** — POST the storyline JSON to the tenant's
  `RemediationPolicy.webhook_url` with a short retry (3 attempts, exp
  backoff up to 2s). PagerDuty / Slack / Opsgenie / generic-HTTP all
  accept this shape.
- **`audit_log`** — Write a tenant-scoped audit row with
  `action=auto_remediation` so the chain captures it for compliance
  exports (DPDP §8(8) "breach detection & response" wants this).

### Persistence layout

```
acp:remediation:ledger:{tenant_id}:{incident_id}   LIST   JSON-encoded RemediationAction rows
acp:remediation:revoked_agents:{tenant_id}         SET    agent_id
acp:remediation:policy:{tenant_id}                 HASH   {revoke_api_keys, kill_active_tokens, page_oncall, audit_log, webhook_url}
```

24 h TTL on the ledger + revoked-agents set (operator-triggered policies
are persisted in DB downstream of Sprint 6). Same TTL as Sprint 4
storylines so the two age out together.

### Idempotency

`execute()` reads the ledger first. If the incident already has any
action with `status=done`, it short-circuits and returns the existing
ledger. The router exposes `/remediation/incidents/{id}/replay` to force
re-run.

### Failure semantics

- **Single-action failure does NOT block the others.** Webhook timeout
  doesn't prevent token revocation.
- **All exceptions are caught and logged.** Remediation is best-effort
  observability; the user request that triggered the incident was
  already blocked.
- **Webhook retries** — 3 attempts, 0.5 s / 1 s / 2 s. After three
  failures the action is marked `failed` with the error message; the
  SOC can replay.

## Success criteria

1. New module `services/security/remediation/policy.py` — `RemediationPolicy`
   dataclass + `policy_for_tenant(redis, tenant_id) -> RemediationPolicy`
   helper (defaults to all-actions-on if no policy stored).
2. New module `services/security/remediation/actions.py` —
   `RemediationAction` dataclass + KIND constants + `to_dict`.
3. New module `services/security/remediation/executor.py` —
   `execute(redis, *, incident_id, tenant_id, agent_id, storyline,
   policy, dry_run=False) -> list[RemediationAction]`.
4. New module `services/security/remediation/webhooks.py` — `post_webhook(url,
   payload, *, retries=3) -> tuple[bool, str]`. Built on httpx.AsyncClient
   so it shares the gateway's pool when invoked from the request path.
5. New router `services/gateway/routers/remediation.py`:
   - `GET /remediation/incidents/{incident_id}` — ledger view.
   - `POST /remediation/incidents/{incident_id}/replay` — force re-run.
   - `GET /remediation/policy` + `PUT /remediation/policy` — read +
     update the tenant's `RemediationPolicy`.
6. Storyline recorder update: when status transitions to `quarantined`
   on an `_commit_one` pass, kick off `executor.execute(...)` as a fire-
   and-forget task.
7. Gateway auth middleware (`_mw_auth.py`) check the revoked-agents set
   and return 401 if hit.
8. Unit tests (target: 12+ pass):
   - `test_remediation_policy_load_default_when_unset`
   - `test_remediation_policy_round_trip`
   - `test_remediation_executor_fires_all_enabled_actions`
   - `test_remediation_executor_idempotent_on_replay`
   - `test_remediation_executor_records_each_action_status`
   - `test_remediation_executor_single_action_failure_does_not_block_others`
   - `test_remediation_executor_dry_run_does_not_mutate_redis`
   - `test_remediation_webhook_retries_on_5xx`
   - `test_remediation_webhook_succeeds_on_2xx`
   - `test_remediation_webhook_marks_failed_after_max_retries`
   - `test_revoked_agents_set_round_trip`
9. Live: deploy + verify
   `POST /remediation/incidents/{id}/replay` returns the ledger as JSON;
   `GET /remediation/policy` returns the default policy; the gateway
   auth middleware rejects a JWT once the agent_id is in the revoked
   set.

## Non-goals

- **Persistent policy in Postgres.** Redis-only for Sprint 6; cold-
  storage policy is operator-managed via the admin UI (Sprint 7+).
- **Webhook signing / mTLS.** Sprint 6 ships a plain POST; HMAC-SHA256
  signing lands in Sprint 7 with the threat-intel pluggable provider
  framework.
- **Slack / PagerDuty / Opsgenie native adapters.** The generic-HTTP
  POST is enough for all of them via their incoming-webhook URLs.
- **Auto-release after operator review.** The revoked-agents set has a
  24 h TTL; operator can manually delete the set entry.
- **Cross-incident grouping.** One remediation pass per incident_id —
  even if two incidents flag the same agent, they each get their own
  ledger.

## Files

**Added:**
- `services/security/remediation/__init__.py`
- `services/security/remediation/policy.py`
- `services/security/remediation/actions.py`
- `services/security/remediation/executor.py`
- `services/security/remediation/webhooks.py`
- `services/gateway/routers/remediation.py`
- `tests/security/test_remediation_policy.py`
- `tests/security/test_remediation_executor.py`
- `tests/security/test_remediation_webhooks.py`

**Touched:**
- `services/security/incidents/recorder.py` — on quarantine transition,
  fire-and-forget `executor.execute(...)`.
- `services/gateway/_mw_auth.py` — check `acp:remediation:revoked_agents`
  set; 401 with `error=agent_revoked_by_remediation` on hit.
- `services/gateway/main.py` — register `_remediation_router`.
- `services/gateway/middleware.py` — `/remediation` path exemption.

## Rollout + rollback

- Deploy + restart `acp_gateway` on both ASG hosts.
- If the executor misbehaves, set `ACP_REMEDIATION_ENABLED=0` and
  restart — Sprint 4 storyline endpoints still work, remediation falls
  back to the old "Redis flag only" behavior.
- The revoked-agents set is operator-clearable via
  `SREM acp:remediation:revoked_agents:{tid} {aid}`.
