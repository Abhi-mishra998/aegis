# ADR-003: Three-layer tenant isolation

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: multi-tenant, security, gdpr, soc2

## Context

Aegis is a multi-tenant SaaS. Every customer record — agent decisions,
audit rows, organizations, users, API keys, kill-switch state — carries
a `tenant_id`. The contractual promise to every customer is: "your data
is invisible to every other tenant, regardless of how a request is
shaped." Cross-tenant data leakage is the single failure mode that ends
the company.

This is a hard problem in practice because tenant identity has to flow
correctly through *every* layer of a request:

- The JWT issuer (Clerk) has to assert the right tenant for the user.
- The HTTP boundary has to validate any `X-Tenant-ID` header against
  the JWT claim, never trust it on its own.
- Every database query has to filter on `tenant_id`. Forget the filter
  on one query, leak a tenant's data.
- A future code change must not let a row land with an `org_id`
  belonging to one tenant and a `tenant_id` belonging to another.

The 2026-06-17 security audit documented a concrete leak from a
**single-layer** design: on skip-listed paths (`/v1/messages`,
`/v1/chat/completions`, `/v1/approvals`, `/slack/*`), the gateway's
`internal_headers()` helper forwarded the client's `X-Tenant-ID`
verbatim to identity-svc + audit-svc. A forged header let tenant B
post escalation cards to tenant A's Slack webhook with tenant B's
prompt excerpt. The leak was caught only because we had a second
layer (DB CHECK constraints) flagging the inconsistent `org_id`
on the resulting row — but the prompt was already gone.

The takeaway: **no single layer is sufficient**. Tenant isolation
has to be enforced redundantly so any one layer can fail without
data leaving the bounds of its tenant.

## Decision

We enforce tenant isolation at **three independent layers**, each with
its own failure-mode story.

### Layer 1 — Identity issuance canonicalisation

When Clerk creates a user OR an organisation, the
`services/identity/webhooks_clerk.py` handler:

- mints a fresh UUID `aegis_tenant_id` for new orgs
  (`services/identity/webhooks_clerk.py:248`),
- writes `aegis_tenant_id` AND `aegis_org_id == aegis_tenant_id` into
  the Clerk org's public metadata (`:280-288`),
- mirrors the mapping into Redis (`acp:org_to_tenant:<clerk_org_id>`)
  so the gateway's JWKS validator can resolve the canonical tenant_id
  from any future JWT without re-reading Clerk
  (`:262-269`).

The Clerk JWT template includes the canonical `aegis_tenant_id` claim;
`sdk/common/clerk_auth.py` reads it from the verified JWT only.

### Layer 2 — Request-state-only propagation in the gateway

`services/gateway/_helpers.py:194-235` (`internal_headers()`) carries
the iron rule: when calling any internal service from the gateway,
`X-Tenant-ID` is sourced from `request.state.tenant_id` (set by the
auth middleware from the validated JWT/API-key), **never** from the
client's `X-Tenant-ID` header. The fall-through to the client header
is reserved for pre-auth contexts that cannot ever reach a tenant-
scoped resource.

The middleware also runs `assert_path_tenant_matches_jwt()` on routes
where the URL itself encodes the tenant (e.g. `/admin/tenants/{id}/
kill-switch`), so a JWT for tenant A cannot operate on tenant B by
changing the URL.

Sprint EI-1 added a parallel `reject_mismatched_tenant_query()` for
the `?tenant_id=X` silent-ignore class (F-S8 brutal-review finding).

### Layer 3 — Database CHECK constraints

`services/identity/alembic/versions/a1b2c3d4e5f6_add_check_constraint_
org_tenant_match.py` defines two Postgres CHECK constraints:

- `ck_users_org_tenant_match` on `users` — `org_id = tenant_id`
- `ck_agent_creds_org_tenant_match` on `agent_credentials` — same

A future code path that tries to insert a row with mismatched IDs
fails at the database with a CheckConstraintViolation; the bug never
reaches a tenant boundary. The SQLAlchemy `enforce_org_id_invariant`
event listener (`services/identity/models.py:363-377`) provides a
matching application-layer defence so the failure mode is "loud
ValueError at flush time" instead of "obscure CheckConstraintViolation
at commit time".

## Alternatives considered

1. **App-only enforcement (single layer).** Cheaper. Rejected — the
   2026-06-17 audit caught a forge that an app-only layer would have
   silently passed. No DB invariant + no canonicalisation at issuance
   = no second chance.
2. **Schema-per-tenant** (a Postgres schema per tenant, set via
   `SET search_path = tenant_xyz`). Considered for absolute isolation.
   Rejected because:
    - Migrations have to run per-tenant — operationally painful at
      ≥ 100 tenants.
    - SaaS-wide aggregate queries (cost reporting, system-wide alert
      tuning) need a UNION across all schemas.
    - The `tenant_id` column + CHECK + ORM filter pattern hits the
      same isolation guarantee at a fraction of the operational cost.
3. **Postgres Row-Level Security (RLS) policies.** Considered. RLS
   gives transparent enforcement at the DB layer, which is appealing.
   Rejected for this iteration because:
    - The session role we use (pgbouncer-pooled, single `aegis`
      user) does not give RLS a per-tenant principal to filter on
      without injecting `SET LOCAL app.tenant_id = …` at the start
      of every transaction — operationally noisy.
    - The CHECK constraint + ORM filter pattern catches mis-tenanted
      writes too, which RLS alone does not (RLS is read-side).
   Re-evaluate when we add a read-side analytics service that uses a
   per-tenant DB role.
4. **One global signing key + tenant context in every payload.** A
   crypto-only design with no DB enforcement. Rejected on the same
   principle as #1 — no second chance against the bug class
   ("forgot to filter").

## Consequences

* **Positive**
  - 7/7 cross-tenant attack vectors blocked in the Sprint EH-1 live
    pentest (DEVELOPER JWT + X-Tenant-ID header spoof; forged JWT;
    cross-tenant audit read; cross-tenant agent enumeration; …).
  - A SOC 2 reviewer can verify the isolation story in three independent
    files; no single change to any one of them silently weakens it.
  - DBA who tries to manually `INSERT INTO users` with mismatched IDs
    fails fast at the CHECK constraint — even with full RDS access.
* **Negative**
  - Three layers means three places to keep in sync when adding a new
    tenant-scoped table. The pattern is templated in the existing
    alembic migrations + the `OrgMixin` / `TenantMixin` in
    `services/identity/models.py` — adding a new table is a copy-paste
    of the constraint block.
  - Pgbouncer-pooled `aegis` role can't get tenant-row-level isolation
    via RLS without per-transaction `SET LOCAL`, so a future bug in
    application code that omits the `WHERE tenant_id =` clause is
    still possible. The CHECK + canonicalisation catches the *write*
    side of that bug; the *read* side is unconstrained by Postgres
    and depends on the ORM filter.
* **Reversibility**
  - **DB CHECK constraints**: trivial to drop, but would constitute a
    fundamental product change customers were sold on (cite the
    Customer Security Package + DPA §5.1).
  - **Canonicalisation at issuance**: 1-day change to webhook
    handler.
  - **Request-state-only propagation**: changing this is hard because
    20+ callers depend on the invariant. Touch with care.

## Implementation references

* `services/identity/webhooks_clerk.py:248,262-269,280-288` — Clerk-side
  canonicalisation
* `sdk/common/clerk_auth.py` — JWT validation extracts canonical
  `aegis_tenant_id` from verified claim only
* `services/gateway/_helpers.py:194-235` — `internal_headers()`
  request-state-only rule
* `services/gateway/_helpers.py:111-130` — `assert_path_tenant_matches_jwt`
* `services/gateway/_helpers.py:133-167` —
  `reject_mismatched_tenant_query` (Sprint EI-1)
* `services/identity/alembic/versions/a1b2c3d4e5f6_add_check_constraint_org_tenant_match.py` —
  DB CHECK constraints
* `services/identity/models.py:363-377` — `enforce_org_id_invariant`
  application-layer defence
* `reports/e2e_test_2026_06_20/isolation_test.sh` — 7-attack matrix
  used by `nightly_verify.yml`

## Verification

```bash
# 1. Confirm both DB CHECK constraints exist on the live RDS.
PGPASSWORD="$DB_PASS" psql -h $IDENTITY_HOST -U aegis -d acp_identity -c \
  "SELECT conname FROM pg_constraint
   WHERE conname IN ('ck_users_org_tenant_match',
                     'ck_agent_creds_org_tenant_match');"
# expect: 2 rows

# 2. Try to forge a tenant via header — must 403.
curl -sS -X GET -H "Authorization: Bearer $TENANT_A_JWT" \
     -H "X-Tenant-ID: $TENANT_B_ID" \
     -o /dev/null -w "%{http_code}\n" \
     https://aegisagent.in/audit/logs
# expect: 403

# 3. Run the full 7-attack matrix that the nightly_verify workflow runs.
BASE=https://aegisagent.in bash reports/e2e_test_2026_06_20/isolation_test.sh
# expect: every attack blocked; 0 cross-tenant rows leaked.
```
