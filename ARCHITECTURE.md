# Architecture

## Service map

12 Python services run as separate containers behind the gateway. Every
service speaks FastAPI; persistent state is PostgreSQL (one schema per
service-owned DB) plus Redis (cache, streams, pub/sub).

| Service           | Port  | Owns                                                         |
|-------------------|-------|--------------------------------------------------------------|
| gateway           | 8000  | Request entry, auth, policy fan-out, response shaping        |
| policy            | 8000  | OPA bundle host + slow-path policy router                    |
| decision          | 8000  | Unified scoring + tier mapping                               |
| audit             | 8000  | Append-only audit log, ed25519 receipts, transparency roots  |
| autonomy          | 8000  | Bounded-autonomy contracts, human-override storage           |
| identity          | 8000  | Users, tenants, orgs, JWT/API-key issuance                   |
| identity_graph    | 8000  | Agent / role / permission / resource graph (Sprint 5)        |
| behavior          | 8000  | Behavior firewall, per-agent baseline                        |
| registry          | 8000  | Agent registration + tool catalog                            |
| insight           | 8000  | Per-tenant aggregations, SOC dashboards                      |
| usage             | 8000  | Billing-grade usage counters, cost caps                      |
| flight_recorder   | 8000  | Per-request timeline (step/snapshot) for replay              |
| forensics         | 8000  | Read-only investigation surface across audit + timeline      |
| api               | 8000  | Tenant-facing API key + incident CRUD                        |
| mcp_server        | —     | Model Context Protocol governance tools                      |
| ui                | 8080  | nginx-served React + Vite admin console                      |

## Request pipeline (gateway middleware)

`services/gateway/middleware.py` runs these 10 phases in order. Phases
return early on the first deny / escalate / quarantine outcome.

```
0  Kill switch        Redis key acp:kill_switch:{tenant_id} → 503
1  Auth               JWT signature + jti revocation, or API-key validation
2  Rate limit         token-bucket per (tenant, agent, token); 429 on overflow
3  Inference          prompt-injection classifier + tool-shape risk score
4  Canonical          normalise raw tool args → CanonicalAction shape
5  Policy             OPA evaluate + local_action_semantics (per-tier hard denies)
6  Behavior           per-minute call window + cumulative risk pipeline
                       (3 Redis ZRANGEBYSCORE calls collapsed to 1 RTT, Sprint 8)
7  Decision           unified score → allow | monitor | escalate | deny | quarantine
8  Enforce            403 + structured findings + auto-remediation hook
9  Filter             output redaction (regex on SSN / PAN / AWS keys / etc.)
10 Audit              chained receipt to Redis stream → audit service worker
```

A normalised CanonicalAction (`services/policy/canonical.py`) is the
unit every rule reads from. Fast-path rules live in
`services/policy/local_action_semantics.py`; the same pattern
catalogs feed an OPA bundle for slow-path callers without JWT claims.

## Security objectives + signal registry

Sprint 1 (`services/security/signal_registry.py`) defines 34 signals
mapped to MITRE ATT&CK tactic + technique. Each signal has a registered
score and tier. The registry is the single source of truth — both the
risk pipeline and the canonical evaluator read scores from it.

Sprint 3 organises detection into per-tactic modules
(`services/security/objectives/`): `initial_access`, `persistence`,
`privilege_escalation`, `defense_evasion`, `credential_access`,
`discovery`, `collection`, `exfiltration`, `impact`.

## Incident model (Sprint 4)

`services/security/incidents/` reconstructs a per-tenant kill chain from
findings. Storage layout (Redis, 24 h TTL):

```
acp:incident:meta:{tenant}:{incident_id}      HASH
acp:incident:steps:{tenant}:{incident_id}     LIST  (JSON steps)
acp:incident:by_session:{tenant}:{session}    STRING → incident_id
acp:incident:by_agent:{tenant}:{agent}        STRING → incident_id
acp:incident:by_xagent:{tenant}:{first_agent} STRING → incident_id
acp:incident:open:{tenant}                    ZSET   member=incident_id score=last_event_ts
```

Grouping order: session → cross-agent chain → 30-min agent fallback.
Pure reconstruction lives in `storyline.py` (no I/O); writer is
`recorder.py`; read API in `store.py`. Read surface:
`GET /storylines`, `GET /storylines/{incident_id}`.

## Identity & Access Graph (Sprint 5)

`services/security/iag/` caches the per-tenant graph
`agent → role → permission → resource` (24 h TTL, refreshed by an
ingestion adapter — Postgres adapter ships; AWS IAM + Vault adapters
deferred).

`compute_blast_radius()` returns the resources an agent **could have**
reached but had NOT yet touched at the time of an incident block.

Read surface: `GET /iag/agents/{agent_id}`,
`GET /iag/incidents/{incident_id}/blast-radius`.

## Auto-Remediation (Sprint 6)

`services/security/remediation/` fires four actions on quarantine:

| Action               | Implementation                                        |
|----------------------|-------------------------------------------------------|
| `revoke_api_key`     | `SADD acp:remediation:revoked_agents:{tenant} {agent}` |
| `kill_active_tokens` | `PUBLISH acp:token:revocations …`                     |
| `page_oncall`        | HTTP POST to operator-configured webhook URL          |
| `audit_log`          | `XADD acp:audit:writes …`                             |

Per-tenant `RemediationPolicy` toggles which actions fire. The auth
middleware checks the revoked-agents set on every request and returns
401 `agent_revoked_by_remediation` on hit.

Read surface: `GET/PUT /remediation/policy`,
`GET /remediation/incidents/{id}`, `POST /remediation/incidents/{id}/replay`,
`POST /remediation/dry-run`.

## Threat-Intel Provider Layer (Sprint 7)

`services/security/threatintel/` ships a pluggable IOC framework. IOC
kinds: `exfil_host`, `c2_domain`, `offshore_token`, `destructive_shell`,
`malicious_path`, `privilege_token`.

Storage: per-tenant Redis sets plus a `_global` cross-tenant overlay.
Providers: `StaticListProvider` (curated defaults — paired with
`services/policy/pattern_catalog.py`) and `HttpFeedProvider` (operator-
configured external feeds, text or JSON, bounded 5xx retry, fail-fast on
4xx).

Read surface: `GET/POST/DELETE /threat-intel/iocs`,
`GET/PUT /threat-intel/feeds/{name}`, `POST /threat-intel/refresh`.

The Sprint 7.5 task to thread runtime IOC matches into the canonical
evaluator (sync code path) is still open; until then the runtime is
additive on top of `pattern_catalog.EXFIL_HOSTS` (Sprint 8 floor).

## Performance + Rego/Python convergence (Sprint 8)

* `services/policy/pattern_catalog.py` is the single source of truth
  for `EXFIL_HOSTS`, `OFFSHORE_TOKENS`, `EXTERNAL_EGRESS_HOSTS`, and
  `PERSONAL_EMAIL_DOMAINS`. Both `canonical.py` and the gateway-internal
  `_session_intelligence.py` import from it.
* `services/policy/rego_emitter.py` generates the corresponding
  Rego set literals between `# --- BEGIN GENERATED:* ---` /
  `# --- END GENERATED:* ---` sentinels inside
  `services/policy/policies/action_semantics_deny.rego`.
  CLI: `python -m services.policy.rego_emitter --check` (CI gate) /
  `--write` (operator).
* `risk_pipeline.cumulative_scores` pipelines three Redis
  ZRANGEBYSCORE calls (session + agent + agent-7d) into a single RTT
  with a sequential fallback on pipeline errors.

## Audit chain + receipts

Every gateway decision produces a receipt. The receipt is:

* JSON envelope with tenant, agent, tool, decision, findings, risk,
  request hash, timestamp;
* signed by the per-tenant ed25519 key stored in SSM (envelope-encrypted
  under the `alias/aegis-audit-envelope` KMS customer key);
* hashed and linked into the audit log's append-only `prev_hash`
  chain;
* batched into a daily Merkle root signed with the same key;
* roots chained via `prev_root_hash`, so an attacker who compromises
  any root key only after a customer has archived an earlier root is
  publicly detectable.

Daily roots are published to a public S3 bucket (`aegis-public-roots-*`)
so anyone — auditor, customer, or third party — can verify the chain
offline using AEVF.

See `services/audit/signer.py`, `services/audit/transparency.py`,
`services/audit/transparency_scheduler.py`,
`docs/AEVF/spec.md`.

## Data model (PostgreSQL, per-service)

The services own non-overlapping schemas. Schemas + Alembic chains
under each service directory (`services/<svc>/alembic/`):

| Schema           | Owner       | Tables                                                |
|------------------|-------------|-------------------------------------------------------|
| `acp_identity`   | identity    | users, tenants, organizations, roles, permissions     |
| `acp_audit`      | audit       | audit_logs, transparency_roots, chain shards          |
| `acp_autonomy`   | autonomy    | playbooks, human_override_events                      |
| `acp_api`        | api         | api_keys, incidents (operator-facing)                 |
| `acp_registry`   | registry    | agents, tools                                         |
| `acp_usage`      | usage       | usage_events, budget_requests, pending_usage_events   |
| `acp_flight`     | flight      | flight_timelines, flight_steps, flight_snapshots      |
| `acp_learning`   | learning    | behavior_profiles                                     |
| `acp_id_graph`   | identity_gr | agent_edges                                           |

PostgreSQL via `pgbouncer` (transaction pooling).

## Redis usage map

| Prefix                                | Purpose                          | TTL    |
|---------------------------------------|----------------------------------|--------|
| `acp:idempotency:{request_id}`        | replay protection                | tiered |
| `acp:apikey:valid:{sha256}`           | API key validation cache         | 60 s   |
| `acp:revoke:{sha256}`                 | JWT/token revocation             | 24 h   |
| `acp:token:revocations` (channel)     | pub/sub for revocation fan-out   | —      |
| `acp:risk:session:{session}`          | sliding 15-min session score     | 24 h   |
| `acp:risk:agent:{tenant}:{agent}`     | sliding 60-min agent score       | 24 h   |
| `acp:risk:agent_7d:{tenant}:{agent}`  | sliding 7-day long-window score  | 8 d    |
| `acp:incident:*`                      | Sprint 4 storyline state         | 24 h   |
| `acp:iag:*`                           | Sprint 5 IAG cache               | 24 h   |
| `acp:remediation:*`                   | Sprint 6 ledger + policy + set   | 24 h   |
| `acp:ti:*`                            | Sprint 7 IOC cache + feed config | 24 h   |
| `acp:quarantine:{tenant}:{agent}`     | behavior-firewall quarantine flag | 24 h  |
| `acp:audit:writes` (stream)           | audit ingestion stream           | —      |

## Deployment topology (prod-ha)

```
                              Internet
                                 |
                    Route 53 (aegisagent.in)
                                 |
                       ALB (acp-prodha-alb, TLS)
                                 |
                +----------------+----------------+
                |                                 |
       EC2 (m6g.medium)              EC2 (m6g.medium)
       AZ ap-south-1a                AZ ap-south-1b
       docker-compose stack          docker-compose stack
       (22 containers)               (22 containers)
                |                                 |
                +-+----------------+--------------+
                  |                |
       RDS PostgreSQL Multi-AZ     ElastiCache Redis
       db.t3.small                 cache.t3.micro × 2
                                   automatic failover
```

Out of band:

* NAT Gateway (egress for ASG instances)
* S3 + DynamoDB gateway VPC endpoints (free, bypass NAT for those services)
* AWS Secrets Manager (runtime credentials)
* AWS KMS customer key (receipt-signing envelope)
* CloudTrail multi-region trail
* S3: `acp-alb-logs-*`, `acp-backups-*`, `aegis-public-roots-*`,
  `aegis-terraform-state-*`, `acp-cloudtrail-*`

The voice agent runs on its own EC2 (`aegis-voice-guide`,
`t3.medium`) in the default VPC; separate deployment.

## See also

* [`docs/architecture/`](docs/architecture/) — extended diagrams, data model, request flow
* [`docs/services/`](docs/services/) — per-service contracts
* [`docs/AEVF/spec.md`](docs/AEVF/spec.md) — audit chain wire format
* [`API.md`](API.md) — HTTP surface
