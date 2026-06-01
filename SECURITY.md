# Security Policy

## Supported versions

| Version  | Status        | Security fixes |
|----------|---------------|----------------|
| `main`   | active        | yes            |
| 0.2.x    | current       | yes            |
| 0.1.x    | maintenance   | critical only  |
| < 0.1    | unsupported   | no             |

Releases are tagged in git; `acp.__version__` matches the tag. The Production
deployment at `aegisagent.in` tracks `main`.

## Reporting a vulnerability

**Do not** open a public GitHub issue for security findings.

Email: `abhishekmishra09896@gmail.com`
Subject line: `[SECURITY] Aegis vulnerability report`

What to include:
- A short description of the vulnerability.
- Steps to reproduce (or proof-of-concept code).
- Affected version / commit SHA (`git rev-parse HEAD` on the running deploy).
- Your suggested remediation if any.
- Whether you would like public credit in the advisory.

We reply within **48 hours** confirming receipt. Substantive triage and a
fix timeline within **7 days**. Critical findings (RCE, auth bypass,
tenant-isolation break) are prioritized over routine work.

## Coordinated disclosure window

- **Day 0:** report received, ack within 48h.
- **Day 1–7:** triage + fix design.
- **Day 7–30:** fix implementation, internal review, regression test.
- **Day 30:** earliest public advisory date (CVE assignment if applicable).
- **Day 90:** absolute disclosure deadline — at this point we publish the
  advisory whether or not the fix has shipped, so that downstream users
  can mitigate.

We will negotiate a different timeline for findings that are particularly
sensitive (e.g. cryptographic break) or particularly low-impact.

## What's in scope

- `aegisagent.in` and all subdomains.
- The code under `services/`, `sdk/`, `infra/`, `scripts/` at any version.
- The Helm chart at `infra/helm/`.
- The OpenAPI surface served at `/openapi.json`.

## What's out of scope (or unlikely to be a finding)

- Findings on third-party dependencies that we have not patched will be
  acknowledged but redirected upstream.
- DoS via volumetric attack. Rate limiting is in scope; load-testing
  designed to overwhelm capacity is not.
- Self-XSS, missing security headers on `/health`, missing CSP on the
  `/openapi.json` documentation page.
- Social engineering of staff.
- Physical attacks on infrastructure.

## Hardening promises that should hold

If you can break any of these, please report:

1. **Tenant isolation.** A token issued for Tenant A must not read or
   modify Tenant B data through any HTTP route.
2. **Audit chain integrity.** Every audit row's `event_hash` must verify
   against `prev_hash + event_fields`. No row should be silently mutable.
3. **Kill switch effectiveness.** When `acp:kill_switch:{tenant_id}` is
   set, every `/execute` for that tenant must return 403. There must be
   no path that bypasses the check.
4. **JWT signature verification.** No path should accept a token without
   a verified signature against the configured `JWT_ALGORITHM`. `none`
   algorithm must be rejected at process startup.
5. **Internal-secret integrity.** Routes that require
   `verify_internal_secret` must reject any request that does not present
   the configured secret. The secret must not have a hardcoded fallback.
6. **Cross-tenant URL-parameter attacks.** A path like
   `POST /decision/kill-switch/{other_tenant_uuid}` must be rejected at
   the gateway even when the caller has a valid JWT for a different
   tenant.

## Cryptographic specifics

- Audit chain uses Ed25519 signatures + SHA-256 `prev_hash` linkage.
- Transparency log roots are sealed daily with the same Ed25519 key.
- Key rotation procedure: `docs/runbooks/key_rotation.md`.
- Public verification key fingerprints: `docs/runbooks/key_rotation.md`
  § "Published key fingerprints."

## What we have done

- Audit chain implementation: `services/audit/writer.py:64-96` (PostgreSQL
  advisory lock + per-row `prev_hash` linkage, verified across 13,400+ rows
  in production with zero violations).
- SSRF guard on autonomy webhook executor:
  `services/autonomy/webhook_executor.py:_assert_safe_webhook_url`.
- Cross-tenant kill-switch protection:
  `services/gateway/_helpers.py:assert_path_tenant_matches_jwt` and
  `services/decision/router.py:_assert_authenticated_tenant_matches`.
- Token revocation via Redis Pub/Sub with sub-second propagation:
  `services/identity/token_service.py:_publish_revocation` and
  `services/gateway/auth.py:run_revocation_listener`.
- Network isolation: every internal service port is bound to `127.0.0.1`
  on the host (`infra/docker-compose.yml`). Only the gateway and UI are
  reachable externally.

## What we have NOT done (honest disclosure)

- External SOC2 Type-II attestation has not been completed. Aegis ships
  evidence-collection artefacts (audit chain, tenant isolation,
  backup/restore scripts) but the formal audit is on the sprint-7+ roadmap.
- The audit log table is not yet partitioned. At very high row counts the
  current schema's query patterns will degrade. The partitioning migration
  is written and gated at
  `services/audit/alembic/versions/v5w6x7y8z9a0_partition_audit_logs.py`
  pending a maintenance window.
- Multi-region failover is not configured. RDS is Multi-AZ; a region-level
  outage would take the platform offline.

## Acknowledgements

Past reporters (with their permission) are credited here.

_(No public advisories yet — this is the v1 of the policy.)_
