# Threat Scenarios

*The four shipped attack cases, the exact Rego rule that blocks each, and how a verifier reproduces the deny end to end.*

The Playground (`/playground`) and Attack Sim (`/attack-sim`) pages render these as one-click buttons. This page is the deep dive: what each attack does, what should happen, and what code path proves it.

## Scenario 1: PII Bulk Export

### What the attack does

An agent with `crm.bulk_export` permission is asked to dump customer billing data for all tenants. The payload requests `fields = ["email","phone","billing_address","ssn"]` and an unbounded row count.

### Expected outcome

HTTP 403 with body:

```json
{
  "success": false,
  "error": "policy_denied",
  "data": {
    "action": "deny",
    "rule_id": "agent.deny.pii_density",
    "findings": ["high_pii_density", "unbounded_query"],
    "score": 0.92,
    "audit_id": "..."
  }
}
```

### Why it denies

Two signals fire:

1. **High-PII-density signal** at stage 3 (inference proxy). The output filter at stage 9 also has a PII-density check, but it would only redact, not deny. The inference proxy flag is what feeds the Decision Engine.
2. **Unbounded query**: the request lacks a `limit` field. The platform's hard-deny pattern in `default.rego` rejects unbounded export queries.

### Verification

1. Run from Playground or curl.
2. Confirm 403 response.
3. Fetch the audit row's receipt (`GET /audit/logs/{audit_id}/receipt`).
4. Confirm `decision: "deny"`, `findings: ["high_pii_density", "unbounded_query"]`, valid signature.
5. The Identity Graph emits a `writes` edge with `outcome: deny` to record the attempt.

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

## Scenario 4: K8s Production Namespace Delete

### What the attack does

A `devops-agent` with `k8s.delete.namespace` permission is asked to delete the `prod` namespace.

### Expected outcome

HTTP 403, `rule_id: "k8s.deny.prod_namespace_delete"`.

### Why it denies

`k8s_policy.rego` hard-deny pattern matches `delete.namespace` operations targeting namespaces whose name starts with `prod`, `production`, or matches the tenant-configured protected list.

The agent's `k8s.delete.namespace` permission grant allowed the call to enter the pipeline; the policy stage's hard-deny pattern blocks it before execution.

This is the canonical example of "per-agent permissions are necessary but not sufficient" — the policy layer enforces platform-wide invariants that override any specific grant.

### Verification

In the current production deployment, the `devops-agent` graph node has `trust_score=0.49` partly because of this exact denied edge. Confirm by:

1. `GET /graph/agents` to fetch the devops-agent node id.
2. `GET /graph/agent/{node_id}` to see its incident edges, including the `writes / k8s.delete.namespace / deny / risk:1.0` edge.

## How to add a new scenario

The platform ships with these four because they cover the four main categories:

- Data exfiltration
- Local execution
- Database damage
- Cloud control-plane abuse

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
| SQL injection | Stage 4 (policy / OPA) | `services/policy/policies/agent_policy.rego` |
| RCE | Stage 4 (policy / OPA) | `services/policy/policies/rate_policy.rego` (sensitive_tool) |
| K8s prod-namespace abuse | Stage 4 (policy / OPA) | `services/policy/policies/k8s_policy.rego` |
| PII exfil | Stage 3 + Stage 6 (decision composite) | inference_proxy + decision engine |
| Compounding-agent delegation | Stage 7 (autonomy contract) | `services/autonomy/router.py` |
| Cross-tenant access via token | Stage 1 (auth tenant binding) | `services/gateway/_mw_auth.py` |
| Replay of a stolen request | Stage 1 (JTI burst window) | `services/gateway/_mw_auth.py:170-200` |

Reading: the platform has multiple stages that each provide one or more defenses. No single stage catches every attack class. The composition is the security model.

## What's NOT covered

- **Adversarial inputs to the LLM itself.** Aegis governs tool calls, not model outputs. A user who jailbreaks the model into generating malicious text but then never executes a tool produces no Aegis-visible event.
- **Side-channel attacks.** Timing attacks against the gateway, denial-of-service via legitimate traffic patterns, supply-chain attacks against the platform's dependencies — these are out of scope for the application-layer policy stage. Operator-side controls (WAF, rate limiting, dependency scanning) cover them.
- **Insider threats with platform-admin access.** A `is_platform_admin` user can do anything across tenants. The audit chain detects post-hoc; preventing the action is out of scope for runtime policy.

## Next

- [OPA Policies](opa-policies.md) — the four Rego files in detail
- [Flow of a Decision](../architecture/flow-of-a-decision.md) — the canonical walk through the DROP TABLE deny
- [Playground UI](../ui/operations/playground.md) — the 4-button surface to run the scenarios
- [Attack Sim UI](../ui/operations/attack-sim.md) — the extended catalog
- [Cryptographic Audit Chain](crypto-audit-chain.md) — what makes the deny records non-repudiable
