# Changelog

All notable changes to Aegis are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where
breaking changes bump the MAJOR.

## [Unreleased]

Rolling sprint output from `sprint-1` through `sprint-6` is described in
the per-sprint completion docs (`sprint-N-completed.md`). The summary
below cross-references the canonical entries so a release-cut script can
pull both.

### Added — security

- Cross-tenant kill-switch protection at gateway + decision service
  (`services/gateway/_helpers.py:assert_path_tenant_matches_jwt`,
  `services/decision/router.py:_assert_authenticated_tenant_matches`).
  Closes the audit-v2 finding that any SECURITY user could engage another
  tenant's kill switch.
- SSRF guard on the autonomy webhook executor — resolves the hostname
  through `socket.getaddrinfo` and rejects RFC-1918 / loopback / link-local
  / metadata IPs before any `httpx` request (`services/autonomy/webhook_executor.py:_assert_safe_webhook_url`).
- `INTERNAL_SECRET` fallback removed; the import now fails fast on a
  missing env var (`services/autonomy/webhook_executor.py:21`).
- `JWT_ALGORITHM` field validator — refuses to start the gateway if
  `JWT_ALGORITHM=none` or anything outside the explicit allow-list
  (`sdk/common/config.py`).
- Docker port bindings — every internal service port now binds to
  `127.0.0.1` on the host. Only `gateway:8000` and `ui:5173` are reachable
  externally (`infra/docker-compose.yml`).
- SSE endpoint no longer accepts JWT in the `?token=` query string —
  prevents token leak via nginx/ALB access logs
  (`services/gateway/main.py:/events/stream`).
- LRU token-cache revocation invalidation via Redis Pub/Sub — revoked
  tokens are rejected within ~1 second on any gateway worker
  (`services/gateway/auth.py:run_revocation_listener`,
  `services/identity/token_service.py:_publish_revocation`).
- SSE mid-stream re-validation every 30 seconds — closes the gap where a
  revoked token's stream stayed alive until disconnect.
- `/admin/tenants` gateway role gate — non-ADMIN/SECURITY tokens receive 403
  instead of being silently proxied with the internal-secret header.

### Added — operations

- Alertmanager wired to Slack + PagerDuty receivers (file-mounted secret
  pattern at `infra/alertmanager.yml`).
- Nightly backup workflow + weekly restore-drill workflow + S3 deploy
  marker (`.github/workflows/scheduled-backup.yml`,
  `.github/workflows/weekly-restore-drill.yml`,
  `docs/runbooks/drill_log.md`).
- Outbox + `pending_usage_events` retention pruner — daily cron
  (`scripts/maintenance/prune_audit_outbox.py`,
  `.github/workflows/scheduled-prune.yml`).
- Worker healthchecks now check a Redis heartbeat key, not just process
  liveness. A stuck consumer loop now triggers a restart
  (`services/insight/worker.py`, `services/groq_worker/service.py`).
- Composite indexes on `audit_logs(tenant_id, timestamp DESC)` +
  `(tenant_id, action, timestamp DESC)` — covers the hot aggregator
  query patterns (`services/audit/alembic/versions/u4v5w6x7y8z9_audit_logs_composite_index.py`).
- Rollback automation — `scripts/ops/rollback.sh` + manual
  `workflow_dispatch` GitHub Action that reads the previous SHA from S3
  and reverts the host.
- Staggered ALB deploy — `scripts/ops/deploy_staggered.sh` drains each
  host from the target group, deploys, smoke-tests, then re-registers
  before moving to the next host.
- CloudWatch Logs aggregation — installer + agent config + IAM policy at
  `infra/cloudwatch/`. Ships every container's stdout/stderr to per-service
  log groups.
- Customer-facing status page — JSON snapshot from
  `scripts/maintenance/publish_status_page.py` + static renderer at
  `infra/statuspage/index.html`.

### Added — code structure

- 6 gateway router modules extracted: `admin`, `decision`, `proxies`,
  `tenant_admin`, `stripe_webhook`, `sso`
  (`services/gateway/routers/*.py`).
- Shared gateway helpers consolidated into `services/gateway/_helpers.py`
  (193 LOC of pure functions; no app-state dependencies).
- `services/billing/` moved under `services/usage/billing_routes/` —
  reflects the README claim "module, not a microservice" and resolves the
  package-level circular import the audits flagged.
- `services/intelligence/` moved to `sdk/intelligence/` — it was a library
  not a service.

### Added — billing

- Stripe webhook end-to-end: signature verification + idempotency claim +
  tier mapping + Postgres update through `PATCH /admin/tenants/{id}`
  (`services/gateway/routers/stripe_webhook.py`,
  `services/identity/router.py:patch_admin_tenant`).
- Tier → quota mapping declarative in `_TIER_QUOTAS` (rps, burst, daily,
  monthly per tier).

### Added — infrastructure as code

- Terraform stub describing the desired AWS state (`infra/terraform/`):
  VPC + 2 AZs, ALB + listener + target group, 2× EC2 with IMDSv2 + SSM +
  CloudWatch agent IAM, RDS Postgres with `multi_az = true`, ElastiCache
  Redis with `multi_az_enabled = true`, S3 buckets with lifecycle rules.
  Awaiting first `terraform import` from operator.
- Terraform CI: `terraform fmt -check -recursive` + `init` + `validate`
  gating every PR touching `infra/terraform/**`.
- Helm chart intent declared (`infra/helm/README.md`); `helm lint` + `helm
  template` dry-render added to PR CI.

### Added — testing

- Playwright e2e suite (`ui/tests/e2e/`): auth, executive dashboard
  (incl. degraded-state banner intercept test), cross-tenant kill-switch,
  audit logs, incidents, observability, agents.
- e2e job in CI spins up a real docker-compose stack and runs the
  Playwright suite (`.github/workflows/test.yml`).

### Added — documentation

- `SECURITY.md` — vulnerability disclosure policy, supported versions,
  hardening promises, honest disclosure of what's not done.
- This `CHANGELOG.md`.
- `CONTRIBUTING.md` — onboarding for new engineers.
- Per-sprint completion docs (`sprint-N-completed.md`).
- `infra/helm/README.md`, `infra/terraform/README.md`, `infra/statuspage/README.md`.

### Removed

- `services/groq_worker/` directory (261 LOC) — duplicate of
  `services/insight/worker.py` per its own docstring.
- 16 `tests/test_phase*_ui.py` files (1,324 LOC) — pure string-match
  contract tests with zero behavioural assertions, now replaced by
  Playwright.
- `ui/src/components/Common/DiffViewer.jsx` (171 LOC) and
  `LiveKpiTile.jsx` (140 LOC) — exported but never imported.
- `GET /graph/compromise/history` route — zero callers in any tested path.
- `postgres_replica` container — application code never read from the
  replica; HA is delegated to AWS RDS Multi-AZ.
- `?token=` query-string fallback on `/events/stream`.

### Changed

- Gateway `main.py`: 3,920 → 3,654 LOC (-6.8%).
- Stripe webhook path added to `_SKIP_PATHS` in middleware (signature
  authn, not JWT).
- Outbox worker classifies 5xx + 408 + 429 as transient (verified during
  the v2 attack-audit — the original "502 misclassified" finding was
  refuted in code).

### Fixed (audit findings closed)

| Finding | Source audit | Closed in |
|---|---|---|
| Cross-tenant kill-switch escalation | principal-engineer-review | sprint-1 |
| SSRF in `fire_generic_webhook` | audit-30 + audit-v2 | sprint-1 |
| `INTERNAL_SECRET` fallback `"change_me_internal"` | audit-30 | sprint-1 |
| `/admin/tenants` not role-gated | audit-30 + audit-v2 | sprint-1 |
| `JWT_ALGORITHM` not validated at startup | audit-30 | sprint-1 |
| Docker ports on `0.0.0.0` | audit-v2 | sprint-1 |
| SSE `?token=` query string | audit-v2 | sprint-1 |
| Alertmanager receivers commented out | audit-30 | sprint-1 |
| LRU revocation latency up to 60s | audit-30 | sprint-2 |
| SSE no mid-stream re-validation | audit-v2 | sprint-2 |
| Outbox + usage_events unbounded growth | principal-engineer-review | sprint-2 |
| Process-alive only healthchecks | audit-v2 | sprint-2 |
| Fire-and-forget DB writes in learning | audit-v2 | sprint-2 |
| Groq idempotency race | audit-v2 | sprint-2 |
| Composite index gap on audit_logs | principal-engineer-review | sprint-1 (migration) |
| `usage ↔ billing` circular import | audit-30 | sprint-3 |
| `services/intelligence` mis-located | audit-30 + audit-v2 | sprint-4 |
| `services/groq_worker` duplicate | audit-30 + audit-v2 | sprint-3 |
| Helm chart intent unclear | audit-30 | sprint-3 |
| String-match phase UI tests | all three audits | sprint-3 (scaffold) + sprint-6 (delete) |
| `postgres_replica` running but unused | principal-engineer-review | sprint-2 |
| `ExecutiveDashboard` silent fallback | audit-v2 | sprint-2 |
| Docker base image not pinned | audit-v2 | sprint-2 |
| No log aggregation | audit-30 + audit-v2 | sprint-4 |
| No rollback automation | audit-30 | sprint-4 |
| No staggered ALB deploy | audit-30 | sprint-4 |
| No customer status page | audit-30 | sprint-4 + sprint-6 |
| 3,920-LOC gateway god-file | all three audits | sprint-3 onward (incremental) |

### Deferred (organisational / external)

- Multi-region failover (sprint-4 backlog) — multi-week initiative.
- SOC2 Type-II attestation — 3-6 month external engagement.
- Hire a second engineer — organisational.
- `terraform import` against the live AWS account — operator action.
- Stripe operator setup (price IDs, webhook URL, secret) — operator action.
- Audit-log partitioning execution (migration written, gated) — maintenance window.
- Decision/audit DB boundary clean fix (live data migration) — maintenance window.

---

## How to read this changelog

Each sprint produced one or more entries in the sections above. The
mapping is:

- Sprint 1 — security blockers + easy wins.
- Sprint 2 — MEDIUM security + operational integration.
- Sprint 3 — refactor + dead-code removal.
- Sprint 4 — Enterprise readiness operational tooling.
- Sprint 5 — router extractions + GDPR routes + Stripe + Terraform.
- Sprint 6 — Stripe completion + Terraform CI + status page renderer + Playwright expansion.

For evidence — `file:path:line` citations for each finding and fix — see
the per-sprint completion docs.
