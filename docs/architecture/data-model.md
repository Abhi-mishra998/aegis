# Data Model

*Every Postgres database, every Redis key family, and every S3 bucket Aegis uses, in one inventory.*

Aegis follows a database-per-service pattern. Each application service owns its tables and is the only writer for them. Cross-service reads are mediated by HTTP, not by sharing a connection string. The exceptions — Forensics reads `acp_audit` directly for replay, and the API service reads from `acp_audit` for incident-to-audit linkage — are documented inline.

All connections go through PgBouncer (`acp_pgbouncer`) on port 6432. The application targets `pgbouncer:6432`, never RDS directly. PgBouncer holds one server-side connection pool per logical database with a configured pool size of 50 each.

## Postgres databases

The platform runs eleven logical Postgres databases. Production lives on AWS RDS `acp-postgres-prod` (single instance with one read replica `acp-postgres-prod-replica`). Local development uses a single `acp_postgres` container that hosts all eleven databases.

| Database | Owner service | Notes |
|---|---|---|
| `acp` | default | The Postgres bootstrap database. Holds `pg_stat_statements` and infrastructure-only state. No application tables. |
| `acp_identity` | identity | Tenants, organizations, users, agent credentials. The source of truth for "who is this caller". |
| `acp_registry` | registry | Agents, tool permissions. The source of truth for "what is this agent allowed to do". |
| `acp_audit` | audit | The signed audit chain, the analyst notes, the transparency roots, the billing outbox. The most write-heavy database. |
| `acp_usage` | usage | Per-tenant usage records, billing events, cost attribution. Drains from the audit outbox. |
| `acp_behavior` | behavior | Behavioral profiles, baselines, anomaly signals. |
| `acp_api` | api | Incidents, API keys, webhooks, SIEM connections, scheduled reports, threat-intel cache. |
| `acp_identity_graph` | identity_graph | Typed graph: nodes, edges, trust scores, drift signals, compromise simulation history. |
| `acp_flight_recorder` | flight_recorder | Per-execution timelines, steps, snapshots, artifacts. |
| `acp_autonomy` | autonomy | Multi-agent contracts, contract violations, human override events, playbooks, playbook runs. |
| `acp_postgres_replica` | (read replica) | Forensics replay queries, heavy aggregator scans, point-in-time recovery target. |

## Tables by service

### identity (`acp_identity`)

| Table | Purpose | Notable columns |
|---|---|---|
| `organizations` | Top-level org grouping above tenants | `id`, `name`, `created_at` |
| `tenants` | Tenant configuration and quotas | `id`, `tenant_id` (UNIQUE), `org_id`, `name`, `tier`, `rpm_limit`, `requests_per_second`, `burst`, `daily_request_cap`, `monthly_request_cap`, `daily_inference_cost_cap_usd`, `degraded_mode_policy`, `is_active` |
| `users` | Human users | `id`, `email` (UNIQUE), `hashed_password`, `role` (`ADMIN`/`SECURITY`/`AUDITOR`/`VIEWER`), `tenant_id`, `org_id`, `is_active`, `last_login` |
| `agent_credentials` | Provisioned secrets for agent-role tokens | `id`, `agent_id`, `secret_hash`, `tenant_id`, `created_at`, `revoked_at` |

Indexes: `users.email` UNIQUE, `tenants.tenant_id` UNIQUE.

### registry (`acp_registry`)

| Table | Purpose | Notable columns |
|---|---|---|
| `agents` | Registered agents | `id`, `name`, `description`, `owner_id`, `status` (`ACTIVE`/`QUARANTINED`), `tenant_id`, `org_id`, `risk_level`, `metadata`, `deleted_at` |
| `permissions` | Tool grants per agent | `id`, `agent_id` (FK → `agents.id`), `tool_name`, `action` (`ALLOW`/`DENY`), `granted_by`, `expires_at`, UNIQUE (`agent_id`, `tool_name`) |

Indexes: `agents.tenant_id`, `agents.deleted_at`, `permissions.agent_id`.

### audit (`acp_audit`)

| Table | Purpose | Notable columns |
|---|---|---|
| `audit_logs` | The signed audit chain. The primary record of every decision Aegis has ever made | `id`, `tenant_id`, `agent_id`, `action`, `decision`, `findings` (JSONB), `metadata_json` (JSONB), `event_hash`, `prev_hash`, `signature`, `key_fingerprint`, `created_at`, `shard` (computed) |
| `transparency_roots` | Daily Merkle roots per tenant, with chain links to the previous day's root | `id`, `tenant_id`, `date`, `merkle_root`, `prev_root_hash`, `leaf_count`, `leaf_range`, `signing_key_fingerprint`, `signature`, `sealed_at` |
| `transparency_historical_keys` | Retired signing keys, kept so old receipts continue to verify after rotation | `key_fingerprint`, `public_key_pem`, `rotated_at`, `tenant_id` |
| `audit_notes` | Analyst-written notes attached to an audit row | `id`, `audit_id` (FK → `audit_logs.id`), `tenant_id`, `created_by`, `note_type`, `body`, `created_at` |
| `pending_usage_events` | Outbox: billing events emitted from audit, drained by the usage worker | `id`, `audit_id`, `tenant_id`, `agent_id`, `amount_usd`, `retry_count`, `created_at` |
| `acp_incidents` | Open security incidents | `id`, `tenant_id`, `title`, `severity`, `status`, `assigned_to`, `created_at` |
| `acp_incident_comments` | Comments on incidents | `id`, `incident_id`, `tenant_id`, `author`, `body`, `created_at` |

Indexes: `audit_logs.tenant_id`, `audit_logs.agent_id`, `audit_logs.created_at`, `audit_logs.action`. `transparency_roots.tenant_id, date` UNIQUE.

The `audit_logs` table grows the fastest. Production deployments enable monthly partitioning by `created_at` (managed externally to the application schema).

### usage (`acp_usage`)

| Table | Purpose |
|---|---|
| `usage_records` | Per-call billable events. One row per allowed `/execute` or escalated decision. Drained from `acp_audit.pending_usage_events`. |
| `usage_dlq` | Dead-letter for usage events that failed to persist after `retry_count` exceeded threshold. Operators inspect via the Billing UI. |

### behavior (`acp_behavior`)

| Table | Purpose |
|---|---|
| `behavior_profiles` (managed by behavior; the `learning` service shares the table) | Per-agent rolling baseline of sequence, velocity, cost, cross-agent signals |
| `behavior_anomalies` | Tagged anomaly events for forensic replay |

### api (`acp_api`)

| Table | Purpose |
|---|---|
| `api_keys` | Tenant API keys with `acp_` prefix; one-way hashed in storage |
| `webhooks` | Outbound webhook configs (Slack, custom) |
| `siem_connections` | SIEM forwarder configs (Splunk, Datadog) |
| `scheduled_reports` | Cron-style report definitions and last-run state |
| `threat_intel_cache` | Cached results from external threat-intel enrichment calls |
| `dashboard_state` | Saved dashboard filters per user |

### identity_graph (`acp_identity_graph`)

| Table | Purpose | Notable columns |
|---|---|---|
| `graph_nodes` | Typed vertices: `agent`, `tool`, `resource`, `tenant`, `human` | `id`, `tenant_id`, `node_type`, `external_id`, `name`, `attributes` (JSONB), `trust_score`, `drift_score`, `last_scored_at`, UNIQUE (`tenant_id`, `node_type`, `external_id`) |
| `graph_edges` | Typed directed edges: `invokes`, `reads`, `writes`, `delegates`, `escalates` | `id`, `tenant_id`, `src_node_id`, `dst_node_id`, `edge_type`, `action`, `outcome` (`allow`/`deny`/`error`), `risk_score`, `request_id`, `attributes`, `occurred_at` |
| `trust_score_history` | Append-only score timeline per node | `id`, `tenant_id`, `node_id`, `score`, `components` (JSONB), `reason`, `captured_at` |
| `drift_signals` | Detected behavior-drift events | `id`, `tenant_id`, `node_id`, `signal_type`, `severity`, `baseline` (JSONB), `observed` (JSONB), `delta`, `detected_at` |
| `compromise_simulations` | Recorded what-if compromise sims for replay | `id`, `tenant_id`, `actor_node_id`, `scenario`, `depth`, `reachable_nodes` (JSONB), `affected_tenants` (JSONB), `blast_radius`, `risk_score`, `summary`, `completed_at` |

Indexes: `graph_nodes.tenant_id, node_type`, `graph_edges.tenant_id, src_node_id`, `graph_edges.tenant_id, dst_node_id`, `graph_edges.occurred_at`.

### flight_recorder (`acp_flight_recorder`)

| Table | Purpose |
|---|---|
| `execution_timelines` | One row per `/execute` request; carries the overall status and the request_id |
| `execution_steps` | Per-stage events within a timeline (gateway stage start/end, decision points) |
| `execution_snapshots` | Pre- and post-decision snapshots of `request.state` for forensic replay |
| `execution_artifacts` | Optional artifacts attached to a step — e.g. tool outputs, JSON dumps |

### autonomy (`acp_autonomy`)

| Table | Purpose |
|---|---|
| `autonomy_contracts` | Multi-agent contract definitions (cross-tenant rules, time windows, delegation depth caps, cost caps) |
| `autonomy_contract_violations` | Recorded violations of an active contract |
| `human_override_events` | When a human operator overrode an automated decision; immutable record |
| `playbooks` | Pre-built remediation workflows |
| `playbook_runs` | Each invocation of a playbook, including `triggered_by` (`auto`, `manual`, `api`) |

## Redis keys

Aegis uses Redis 7 (ElastiCache `acp-redis-prod` in production, `acp_redis` container locally). Two databases: db 0 for runtime state, db 1 for queues and outboxes.

| Key pattern | Purpose | TTL |
|---|---|---|
| `acp:kill_switch:{tenant_id}` | Stage 0 kill-switch flag | None — cleared on disengage |
| `acp:revoked_jti:{jti}` | Stage 1 JWT revocation by JTI | Until the JWT would have expired |
| `acp:revoked_tokens:{token_hash}` | Stage 1 JWT revocation by SHA-256 fingerprint | Until expiry |
| `acp:jti_last_used:{jti}` | Stage 1 replay window | 1 second |
| `acp:auth_fail:{ip}` | Stage 1 per-IP failed-auth counter | 5 minutes |
| `acp:ratelimit:{tenant_id}:tokens` + `:refill_at` | Stage 2 token bucket state | None — refilled via Lua |
| `acp:agent_cost_today:{agent_id}:{YYYYMMDD}` | Stage 2 per-agent USD cost accumulator | 26 hours |
| `acp:agent_cost_cap:{agent_id}` | Stage 2 per-agent USD cost cap override | None — manual write |
| `acp:policy_decision:{request_hash}` | Stage 4 OPA decision cache | Tier-dependent: enterprise 24h, premium 1h, basic 5m |
| `acp:behavior_score:{agent_id}` | Stage 5 most-recent score cache | 60 seconds |
| `acp:signal_weights:{tenant_id}` | Stage 6 Decision Engine weights override | None |
| `acp:audit_events` (Stream) | Stage 10 audit outbox | Untrimmed; XACK on processed |
| `acp:audit_chain_lock:{tenant_id}` | Audit worker serialization | 5 seconds (SETNX) |
| `acp:audit_chain_tail:{tenant_id}` | Cached last-event_hash for fast chain link | 1 hour, refreshed on write |
| `acp:sse:tenant:{tenant_id}` (Pub/Sub channel) | Server-Sent Events fanout for Live Feed | N/A — pub/sub |
| `acp:sse:agent:{agent_id}` (Pub/Sub channel) | Per-agent SSE fanout | N/A |
| `acp:groq_events` (Stream) | Inference event ingestion from agents | Untrimmed; XACK on processed |
| `acp:billing_alerts` (List) | Per-tenant 80%-of-monthly-cap alert queue | None |
| `acp:reconcile_cursor:{tenant_id}` | Cursor for the daily audit↔usage reconciliation job | 30 days |
| `acp:transparency_root_lock:{date}` | Single-writer guard on daily root sealing | 1 hour |
| `acp:sso_config:{tenant_id}` (Hash) | Per-tenant SSO provider config | None |

## S3 buckets

| Bucket | Use | Lifecycle |
|---|---|---|
| `acp-receipts-prod` | Per-execution signed receipts in JSON; one object per audit row | Indefinite retention |
| `acp-backups-prod` | Nightly `pg_dump` encrypted with `age`, plus `transparency_roots` snapshots | 90 days standard, 7 years cold storage for compliance |
| `acp-tenant-exports-prod` | Per-tenant TAR exports generated by `scripts/ops/export_tenant.py` (right-to-portability) | 30 days from issue, then auto-deleted |
| `acp-fix-{stamp}` | Ad-hoc deploy bundles dropped during a deploy and consumed by SSM. Not authoritative; cleared periodically | 7 days |

## Cross-database invariants

Two invariants must hold across services. They are enforced in code and verified by tests:

1. **`org_id == tenant_id` at the tenant level.** Aegis is a multi-tenant SaaS; the org-vs-tenant distinction exists for future use but is constrained to be equal today. The check lives in `sdk/common/invariants.py::assert_org_consistency` and runs on every user login and every agent registration.
2. **Every `usage_records` row corresponds to exactly one `audit_logs` row.** The outbox pattern guarantees one-to-one. The daily reconciler `scripts/ops/reconcile.py` checks both directions and emits Prometheus gauges `acp_reconcile_audit_without_usage` and `acp_reconcile_usage_without_audit`. A nonzero gauge is an alert.

## Schema management

Each service ships an Alembic configuration. Migration trees live at `services/{service}/alembic/versions/`. Production deployments run migrations as part of the deploy SSM script. Local development applies them on container start.

The `acp_postgres_replica` is fed by Postgres's built-in streaming replication; it is not a separate Alembic target.

## What's NOT in Postgres

- Free-form telemetry — Prometheus and Jaeger have their own stores.
- LLM prompts and completions — Aegis records the audit row including findings and the canonical request shape, but does not store full prompt text by default. Some tenants opt in to prompt-capture; the data lives in `acp_audit.audit_logs.metadata_json` when present.
- Compliance PDFs — generated on demand from `audit_logs` and not persisted between requests.

## Next

- [System Overview](system-overview.md) — the service graph that owns these tables.
- [10-Stage Pipeline](10-stage-pipeline.md) — which Redis keys each stage reads or writes.
- [Multi-Tenancy](multi-tenancy.md) — how `tenant_id` reaches every row.
- [Backup & Restore](../operations/backup-restore.md) — how `pg_dump` and S3 lifecycle policies cover all the above.
