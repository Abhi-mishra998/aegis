# Threat Scenarios

*The shipped attack cases, the exact Rego rule that blocks each, and how a verifier reproduces the deny end to end.*

The Playground (`/playground`), Attack Sim (`/attack-sim`), and the Live
Demo (`/live-demo`, with the R5 scenario picker — `fintech_data_egress`,
`devops_destruction`, `support_pii_exfil`) render these as one-click buttons. This page
is the deep dive: what each attack does, what should happen, and what code
path proves it.

As of 2026-06-14 the platform covers more than the original four scripted
demos. The R0 + v3-deep sprint shipped
`services/policy/policies/action_semantics_deny.rego` which denies on the
*meaning* of the action (not on a substring match), with risk-tunable
thresholds and K8s namespace awareness. See
[OPA Policies](opa-policies.md#action_semantics_denyrego--r0--v3-deep) for the
full rule walkthrough.

## Scenario 1: PII Bulk Export (risk-tunable threshold, v3-deep)

### What the attack does

An agent with `crm.bulk_export` (or any tool whose payload contains
`SELECT … FROM customers` with an explicit `LIMIT 10000`) is asked to dump
customer billing data. The payload requests `fields = ["email","phone",
"billing_address","ssn"]` and the row count exceeds the agent's
risk-level-derived threshold.

### Expected outcome

HTTP 403 with body:

```json
{
  "success": false,
  "error": "policy_denied",
  "data": {
    "action": "deny",
    "rule_id": "policy:semantic:pii_bulk_export_medium",
    "findings": ["high_pii_density", "pii_bulk_export_breach"],
    "score": 0.92,
    "audit_id": "..."
  }
}
```

The reason-string suffix (`_low` / `_medium` / `_high` / `_critical`)
encodes which risk-tier threshold tripped — exactly which row count is
"too many" depends on the agent's risk level.

### Why it denies

Two layers fire:

1. **High-PII-density signal** at stage 3 (inference proxy). The output
   filter at stage 9 also has a PII-density check, but it would only
   redact, not deny.
2. **`action_semantics_deny.rego._pii_row_threshold_breached`** at stage 4.
   The gateway middleware (`services/gateway/middleware.py`) extracts the
   SQL `LIMIT` into `input.payload.row_limit` ahead of OPA. The Rego rule
   selects the threshold from a risk-tunable table:

   | Risk level | Row threshold (deny above) |
   |---|---|
   | `low` | 10,000 |
   | `medium` | 1,000 |
   | `high` | 100 |
   | `critical` | 0 (any bulk PII export denies) |

   Same prompt against a `low`-risk agent produces an allow at 5,000 rows;
   against `high` it produces a deny. The picker is `input.agent.risk_level`
   with a `medium` default if the field is absent.

### Verification

1. Run from Playground / Live Demo (`fintech_data_egress` scenario) / curl.
2. Confirm 403 response.
3. Fetch the audit row's receipt (`GET /audit/logs/{audit_id}/receipt`).
4. Confirm `decision: "deny"`, the suffixed `rule_id`, valid signature.
5. Confirm the AEVF bundle (any framework export covering this row) carries
   the same `event_hash` and re-verifies offline via
   `pip install aegis-aevf && aegis-verify --bundle bundle.json`.
6. The Identity Graph emits a `writes` edge with `outcome: deny` to record the attempt.

## Scenario 2: RCE via `rm -rf /`

### What the attack does

An agent with `shell.exec` permission receives the prompt-injected command `rm -rf /`.

### Expected outcome

HTTP 403, `rule_id: "agent.deny.destructive_shell"`.

### Why it denies

`rate_policy.rego` and `default.rego` together hit:

```rego
sensitive_tool if {
    destructive_tools := {"delete", "drop", "truncate", "exec", "shell", "sudo", "rm"}
    some t in destructive_tools
    ...
}
```

The destructive-shell regex catches `rm -rf` plus a long list of similar patterns. The agent's risk score is also elevated by the inference proxy detecting the shell-metacharacter pattern.

### Verification

Same flow: run, confirm 403, fetch receipt, verify chain.

## Scenario 3: SQL Injection — `DROP TABLE`

### What the attack does

The Quickstart's canonical example. The agent runs:

```sql
SELECT * FROM customers; DROP TABLE customers;
```

### Expected outcome

HTTP 403, `rule_id: "agent.deny.destructive_sql"`.

### Why it denies

The Rego rule (illustrative; actual rule lives in the deployed bundle):

```rego
package acp.v1.agent

deny[rule] {
    input.tool_name == "db.query"
    regex.match(`(?i)\bdrop\s+table\b`, input.payload.query)
    rule := {"id": "agent.deny.destructive_sql", "severity": "critical"}
}
```

The match is case-insensitive and matches `DROP TABLE` anywhere in the query string. The inference proxy at stage 3 also flags the `;` followed by a destructive verb as multi-statement injection.

### Verification

See [Flow of a Decision](../architecture/flow-of-a-decision.md) for the full walked example of this exact scenario.

## Scenario 4: K8s Production Namespace Delete (namespace-aware, v3-deep)

### What the attack does

A `devops-agent` with `k8s.delete.namespace` permission is asked to
`kubectl delete ns prod` (or any namespace whose name contains a production
marker). Dev/test namespace deletes are allowed so the rule does not
over-deny — the deny is *targeted at production*, not at the verb.

### Expected outcome

HTTP 403, `rule_id: "policy:semantic:k8s_prod_destruction"`.

### Why it denies

`action_semantics_deny.rego._k8s_prod_destruction` (the v3-deep helper)
checks `input.payload.k8s_namespace` (extracted by the gateway middleware
from `kubectl`/`helm` shell commands) against a hard-coded list of
production markers:

```
{prod, production, customer, payments, billing, staging, live, mainnet}
```

If the namespace name contains any substring from this set, the deny
fires. Targets like `dev`, `test`, `qa`, `feature-foo`, `sandbox` pass
the check. The Live Demo's `devops_destruction` scenario picker uses this rule
to produce its mixed allow/deny trace — same `kubectl delete ns` verb
across multiple targets, only the prod-marker ones deny.

The agent's `k8s.delete.namespace` permission grant allowed the call to
enter the pipeline; the policy stage's hard-deny pattern blocks it before
execution.

This is the canonical example of "per-agent permissions are necessary but
not sufficient" — the policy layer enforces platform-wide invariants that
override any specific grant.

### Verification

In the current production deployment, the `devops-agent` graph node has
`trust_score=0.49` partly because of this exact denied edge. Confirm by:

1. `GET /graph/agents` to fetch the devops-agent node id.
2. `GET /graph/agent/{node_id}` to see its incident edges, including the
   `writes / k8s.delete.namespace / deny / risk:1.0` edge.

## Scenario 5: External-domain PII exfil (v3-deep, A5)

### What the attack does

A support agent is asked to email a customer record (or webhook-POST it,
or HTTP-POST it) to `someone@gmail.com` — a domain that is not in the
tenant's allow-list of internal recipients.

### Expected outcome

HTTP 403, `rule_id: "policy:semantic:external_exfil"`.

### Why it denies

`action_semantics_deny.rego._external_exfil` checks the recipient domain
on three send paths:

| Tool | Field inspected |
|---|---|
| `email.send` | `input.payload.to` recipient domain |
| `http.post` | `input.payload.url` host |
| `webhook.send` | `input.payload.target_url` host |

If the domain is not in the per-tenant allow-list AND the payload contains
PII markers (`email`, `ssn`, `phone`, etc.), the rule denies. Same prompt
to an internal recipient passes; rewriting it to an external recipient
denies — Live Demo's `support_pii_exfil` scenario picker demonstrates this.

### Verification

1. Run `support_pii_exfil` scenario from Live Demo, or POST directly with an
   external recipient.
2. Confirm 403, the `rule_id` above, the `external_exfil` finding.
3. Verify the audit row's signature; the same event_hash appears in any
   AEVF bundle export covering the period.

## How to add a new scenario

The platform now covers six categories of attack:

- Data exfiltration (Scenario 1 — risk-tunable PII threshold)
- Local execution (Scenario 2 — `_shell_destruction`)
- Database damage (Scenario 3 — `_sql_ddl_destruction`)
- Cloud control-plane abuse (Scenario 4 — `_k8s_prod_destruction`)
- External-domain PII exfil (Scenario 5 — `_external_exfil`)
- System-path access (`/etc/passwd`, `~/.ssh/id_*` — `_system_path_access`)

To add a fifth (e.g., "API key exfiltration via email"):

1. Define the payload as a function in `ui/src/pages/AttackSimulation.jsx` and `AgentPlayground.jsx`.
2. Confirm the platform-shipped Rego file already covers it. If not, add a hard-deny rule to the appropriate `.rego` file (or to a tenant overlay for tenant-specific patterns).
3. Add a unit test in `services/policy/policies/*_test.rego` so the rule cannot regress.

## Behavioral firewall — degraded mode

A separate behavioral firewall scenario covers what happens when the Behavior service is down. The per-tenant `degraded_mode_policy` decides:

- `block_high_risk` (default): treats the behavior signal as "high" if any other stage already flagged risk. Otherwise treats as low. Reasonable balance.
- `block_all`: every non-trivial decision denies. Maximum security; some legitimate traffic blocks.
- `allow_with_audit`: proceed with `behavior_score=0`. Maximum availability; some attacks slip past.

The audit row records `behavior_skipped=true` and `service_status="degraded"` so the operator sees what state the firewall was in at decision time.

## Per-attack defense layers

| Attack | Stage that fires | Source |
|---|---|---|
| Prompt injection | Stage 3 (inference proxy) | `services/gateway/inference_proxy.py` |
| SQL injection / DDL destruction | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._sql_ddl_destruction` |
| Shell RCE (`rm -rf`, `mkfs`, `dd of=/dev/...`) | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._shell_destruction` |
| K8s prod-namespace abuse | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._k8s_prod_destruction` (v3-deep, namespace-aware) |
| PII bulk export (risk-tunable) | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._pii_row_threshold_breached` (v3-deep) |
| External-domain PII exfil | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._external_exfil` (A5/v3-deep) |
| System-path access (`/etc/passwd`, `~/.ssh/id_*`) | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego._system_path_access` |
| Critical-risk destructive tool | Stage 4 (policy / OPA) | `services/policy/policies/action_semantics_deny.rego` |
| Compounding-agent delegation | Stage 7 (autonomy contract) | `services/autonomy/router.py` |
| Cross-tenant access via token | Stage 1 (auth tenant binding) | `services/gateway/_mw_auth.py` |
| Replay of a stolen request | Stage 1 (JTI burst window) | `services/gateway/_mw_auth.py:170-200` |

Reading: the platform has multiple stages that each provide one or more defenses. No single stage catches every attack class. The composition is the security model.

## What's NOT covered

- **Adversarial inputs to the LLM itself.** Aegis governs tool calls, not model outputs. A user who jailbreaks the model into generating malicious text but then never executes a tool produces no Aegis-visible event.
- **Side-channel attacks.** Timing attacks against the gateway, denial-of-service via legitimate traffic patterns, supply-chain attacks against the platform's dependencies — these are out of scope for the application-layer policy stage. Operator-side controls (WAF, rate limiting, dependency scanning) cover them.
- **Insider threats with platform-admin access.** A `is_platform_admin` user can do anything across tenants. The audit chain detects post-hoc; preventing the action is out of scope for runtime policy.

## Next

- [OPA Policies](opa-policies.md) — the five shipped Rego files in detail, including the v3-deep `action_semantics_deny.rego`
- [Flow of a Decision](../architecture/flow-of-a-decision.md) — the canonical walk through the DROP TABLE deny
- [Playground UI](../ui/operations/playground.md) — the 4-button surface to run the scenarios
- [Attack Sim UI](../ui/operations/attack-sim.md) — the extended catalog
- [Cryptographic Audit Chain](crypto-audit-chain.md) — what makes the deny records non-repudiable
