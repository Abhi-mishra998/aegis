# ADR-004: OPA Rego for policy decisions, not inline Python

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: policy, opa, rego, security, governance, hot-path

## Context

Aegis's contractual surface is "deny the wrong AI action before it
fires." Every tool call from a customer's agent runs through the
policy layer: large wire transfer above a customer-set floor, kubectl
delete against a production namespace, bulk PII export above a row
threshold, SQL with `WHERE 1=1`, etc. The decision must be:

- **Tenant-scoped** — Tenant A's "deny wire > $100k" rule must not
  apply to Tenant B who set theirs at $500k.
- **Hot-reloadable** — a customer who tightens their wire-transfer
  cap at 14:30 must see the new rule active by 14:30:01, not at the
  next deploy.
- **Auditable to a regulator** — "show me the rule that denied this
  decision" must produce a readable text artefact, not a Python
  function signature.
- **Fail-CLOSED** — when the policy decision engine is unreachable,
  the answer is `deny`, never `allow` and never `unknown`.
- **Off the hot path of every other service** — the policy lookup is
  on the request path of every `/execute` call (the entire product),
  so it cannot fan out across multiple Python services per request.

## Decision

We will run **OPA (Open Policy Agent)** as a sidecar container
(`acp_opa`, image `openpolicyagent/opa:1.17.1-debug` SHA-pinned per
`infra/docker-compose.yml:113`), evaluating Rego policy files at
`services/policy/policies/*.rego`:

- `default.rego` — base allow/deny semantics
- `agent_policy.rego` — per-agent action permissions
- `action_semantics_deny.rego` — wire-transfer thresholds, k8s prod
  denies, bulk PII denies
- `k8s_policy.rego` — k8s-specific tool-arg shape rules
- `rate_policy.rego` — per-agent rate-limit policy

The policy service (`services/policy/`) is a thin Python wrapper
around OPA: it normalises tool calls into a canonical action model
(`services/policy/canonical.py`), passes the canonical action to OPA
as the input document, and translates OPA's allow/deny/escalate
verdict back into the gateway's response shape.

OPA's URL is configured via `OPA_URL` env var; `OPA_FAIL_MODE=closed`
(default) means any OPA failure — timeout, 5xx, missing — returns
`deny` to the caller. `closed` is the only sane mode for production;
the `open` mode exists only for local dev so a developer who hasn't
started OPA can still iterate.

Per-tenant policy bundles are mounted into OPA at
`/policies/<tenant-uuid>/` so a tenant's overlay rules (e.g. their
own wire-transfer threshold) live in their own bundle path.

## Alternatives considered

1. **Pure-Python rules in the policy service**, e.g. a `RuleEngine`
   class with `@rule` decorators on methods. Rejected because:
    - Auditor needs to read Python to know what a rule actually does;
      Rego is purpose-built for this and reads like English ("deny if
      input.action == 'wire_transfer' and input.amount_usd > 100000").
    - Hot-reload is hard without restarting the service — OPA reloads
      bundle files in-place every 30 seconds.
    - "Show me the rule" produces a Python function reference; Rego
      produces a text rule.
    - SOC 2 / EU AI Act §12 want declarative policy artefacts. Python
      decorators don't satisfy that on their own.
2. **Cedar** (AWS's open-source policy language). Rejected — younger
   ecosystem (2023+), less Kubernetes / Sigstore / industry use,
   no comparable bundle-distribution story. Re-evaluate in 2027 if
   AWS shifts IAM to Cedar internally.
3. **Custom DSL** parsed into Python. Rejected — building a policy
   language is a 2-year detour from the actual product.
4. **Pure-Rego with NO Python wrapper** (gateway calls OPA directly).
   Tempting but rejected — the canonical-action normalisation
   (`services/policy/canonical.py`) is non-trivial (tool-name
   aliases, arg shape coercion, dollar-amount unit conversion). Doing
   it in Rego would push complex string handling into a language
   that's not great at it. The Python wrapper is the right place.
5. **Inline rules co-located with the gateway middleware** (no
   separate policy service or OPA at all). Rejected — couples the
   gateway deploy cadence to the policy-rule cadence, defeats the
   "tenants can hot-reload their own rules" goal, and ties up the
   gateway's CPU budget on every policy evaluation.

## Consequences

* **Positive**
  - 6 prod policy files, ~700 lines of Rego total — a customer's
    counsel can read them all in one sitting.
  - Per-tenant policy bundles isolate rule changes — tenant A can
    tighten their wire cap without touching tenant B's bundle.
  - OPA fails closed by default; the only way an Aegis incident
    drops to "policy unknown" is if `OPA_FAIL_MODE=open` is
    deliberately set, which production never does.
  - SOC 2 + EU AI Act §12 evidence is "read this Rego file" — done.
  - Sigstore + Sigstore-Rego are an industry standard adjacent path;
    we can plug into the same ecosystem when we add policy-bundle
    signing.
* **Negative**
  - One more process to operate (the OPA sidecar). At our compose
    scale this is trivial; at K8s scale it's a DaemonSet pattern OPA
    is well-suited to.
  - +5-10 ms per policy decision vs inline Python. Acceptable —
    every /execute call already has 50-100 ms of work; OPA is < 10%
    of that budget.
  - Rego has a learning curve; a new engineer joining adds ~1 day
    to ramp on the language. The 700-line corpus is small enough to
    read end-to-end in a morning.
* **Reversibility**
  - **Hard.** Inlining the rules back into Python would lose the
    tenant-bundle, hot-reload, and declarative-evidence properties
    that make the product credible to regulators. 6+ weeks of
    re-design to undo.

## Implementation references

* `services/policy/policies/default.rego` — base allow/deny + skip
  semantics
* `services/policy/policies/agent_policy.rego` — per-agent ACLs
* `services/policy/policies/action_semantics_deny.rego` — wire
  thresholds, k8s prod denies, bulk PII denies
* `services/policy/policies/k8s_policy.rego` — k8s tool-arg shape
* `services/policy/policies/rate_policy.rego` — rate-limit policy
* `services/policy/canonical.py` — canonical action model
* `services/policy/local_action_semantics.py` — Python-side fast-path
  for the same rules (used when OPA is unreachable AND
  `OPA_FAIL_MODE=open` — local dev only)
* `infra/docker-compose.yml:106-115` — pinned OPA image
* `sdk/common/config.py` — `OPA_URL` + `OPA_FAIL_MODE` settings
* `tests/policy/` — end-to-end tests against a real OPA sidecar

## Verification

```bash
# 1. Confirm the OPA image is SHA-pinned (not a moving tag).
grep -E 'openpolicyagent/opa:.*@sha256:' infra/docker-compose.yml
# expect: 1 match — image at 1.17.1-debug pinned to a specific digest

# 2. Confirm fail-closed default in the running gateway.
docker exec acp_gateway env | grep OPA_FAIL_MODE
# expect: OPA_FAIL_MODE=closed

# 3. Fire one of the deny rules and verify the Rego identifier shows
#    up in the response findings array — proving the chain
#    canonical → OPA → response is intact.
curl -sS -X POST -H "Authorization: Bearer $JWT" \
  -d '{"tool":"wire_transfer","payload":{"amount_usd":250000,"recipient":"external"}}' \
  https://aegisagent.in/execute | jq '.findings'
# expect: includes "money_transfer_external" and policy id from a Rego rule.
```
