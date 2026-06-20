# ADR-002: Centralised RBAC matrix — (path, method) → roles map

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: authz, rbac, gateway, security, audit

## Context

Before Sprint EH-1 (2026-06-20), Aegis treated authenticated == authorised.
A `DEVELOPER` role JWT could call `/compliance/export` and get the SOC 2
evidence ZIP for its own tenant. The brutal-review audit run on
2026-06-19 enumerated this as the #1 P0 security gap:

> Out of ~235 authed routes, 5 enforce role. The rest are "authenticated
> == authorized." A DEVELOPER-role JWT can hit /compliance/export,
> /audit/logs, /forensics/*, /incidents, mint API keys via /api-keys, etc.

The fix had to satisfy three constraints simultaneously:

1. **Auditable in one place.** A CISO or SOC 2 auditor must be able to
   read *one file* and see every protected route + its role gate.
   Decorators scattered across 30+ router files fail this — the auditor
   has to grep, infer, and trust they got them all.
2. **Testable in one shot.** Unit tests must enumerate every rule and
   assert the matrix can't drift in PRs that touch routes.
3. **Cheap to extend.** Adding a new endpoint must not require a
   security review of how-to-add-a-decorator; the convention should be
   "add a row to the rule list, add a row to the test".

## Decision

We will keep all role rules in **one file**:
`services/gateway/_rbac_map.py:70-218`. The file exports:

- a `_R(pattern, methods, *, roles=(), min_role=None)` constructor for
  declarative rules,
- a `_RULES` list of every protected (path, method, role-set) tuple,
- an `is_authorized(path, method, actual_role) -> (bool, reason)`
  function the gateway middleware calls from a single chokepoint
  (`services/gateway/middleware.py:491-493`).

Rules support both `roles=(…)` (exact match) and `min_role=…` (role
hierarchy walk OWNER > ADMIN > SECURITY_ANALYST > DEVELOPER > READ_ONLY).
Patterns are glob-style: `*` matches one path segment, trailing `*`
matches any suffix. Most-specific rule wins via registration order.

The matching test file at `tests/test_rbac_matrix.py` is **parametrized
over every cell** (77 cases at the time of writing); a PR that adds a
new route OR changes a role gate must add the matching test row in the
same diff. CI fails otherwise.

The human-readable mirror at `docs/security/rbac_matrix.md` is the
artefact a SOC 2 auditor reads; it is generated from the same source
of truth.

## Alternatives considered

1. **`@require_role(…)` decorator per route.** The framework-native
   pattern. Rejected because:
    - Auditing requires reading every router file (`services/gateway/
      routers/*.py`, ~30 files).
    - It is silent when missed — a route without the decorator is a
      hole that nothing in CI catches.
    - The test surface fragments across 30 test files (or worse, no
      tests at all, which was the prior state).
2. **OPA-based authz** (every request goes through an OPA query before
   reaching the handler). Rejected because OPA is already in the path
   for tool-call decisions (`services/policy/policies/*.rego`) and
   adding it for route-level RBAC doubles the OPA hot path. Per-request
   latency budget on management endpoints is ~50 ms; OPA query adds
   ~5-10 ms even on a healthy sidecar.
3. **FastAPI `Security(…)` dependency injection** with a custom
   scope-bag. Same coupling problems as decorators (per-route
   declaration) plus a FastAPI lock-in we don't need.
4. **Two files: one for "deny by default" + a separate allow-list.**
   The deny-by-default semantic was tempting (every endpoint blocked
   unless allow-listed), but it conflicts with the existing public
   surface (`/health`, `/trust`, `/demo/spawn-workspace`, `.well-known/
   security.txt`) — those routes have no role requirement at all, and
   force-listing them feels worse than the current allow-listed roles
   pattern. Re-evaluate at scale ≥ 1k routes.

## Consequences

* **Positive**
  - One file, 218 lines, an auditor can read in 10 minutes.
  - 77 parametrized tests; a missed update fails CI on the same PR.
  - Adding a route is mechanically obvious: append a `_R(…)` row, append
    test cases.
  - The brutal-review #1 P0 is closed verifiable (EH-1: 7/7 DEVELOPER-
    token attack vectors against the live prod now return 403).
* **Negative**
  - One central file becomes a merge-conflict hot spot when 2 PRs add
    routes simultaneously. Acceptable at solo-founder scale; revisit at
    team ≥ 5 backend engineers.
  - Glob patterns are deliberately simple — no regex, no path-parameter
    capture. A handler that needs "OWNER for path-tenant == JWT tenant"
    must call the additional helper
    `assert_path_tenant_matches_jwt()` in `_helpers.py:111-130`.
* **Reversibility**
  - **Trivial** — the rule table can be migrated row-by-row into
    decorators without touching any handler, in case OPA-based authz
    ever becomes attractive.

## Implementation references

* `services/gateway/_rbac_map.py:70-218` — rule constructor + rule list +
  `is_authorized`
* `services/gateway/middleware.py:488-493` — single middleware chokepoint
  that calls `is_authorized` after auth phase
* `services/gateway/_helpers.py:111-130` —
  `assert_path_tenant_matches_jwt` companion for path-tenant routes
* `services/gateway/_helpers.py:133-167` —
  `reject_mismatched_tenant_query` (Sprint EI-1 / F-S8 fix)
* `tests/test_rbac_matrix.py` — 77 parametrized cases
* `docs/security/rbac_matrix.md` — human-readable mirror

## Verification

```bash
# 1. Count rules (sanity-check the file isn't empty).
grep -c '^    _R(' services/gateway/_rbac_map.py
# expect: > 60 (current 60+ rules across 17 prefix groups)

# 2. Run the parametrized matrix tests — must all pass.
PYTHONPATH=. python3 -m pytest tests/test_rbac_matrix.py -q --import-mode=importlib
# expect: 77 passed

# 3. Live pen-test against the deployed gateway with a DEVELOPER JWT —
#    every one of the 7 well-known-protected routes must return 403.
for path in /compliance/export /api-keys /audit/logs/export \
            /admin/tenants /billing/checkout \
            /workspace/exit-shadow-mode /forensics/replay/agt; do
  echo -n "$path → "
  curl -sS -o /dev/null -w "%{http_code}\n" -X GET \
    -H "Authorization: Bearer $DEVELOPER_JWT" \
    "https://aegisagent.in$path"
done
# expect: 403 403 403 403 403 403 403
```
