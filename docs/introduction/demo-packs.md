# Demo Packs

*Three scripted scenarios that drive every UI page from empty to populated. Each pack runs in 10–25 seconds end-to-end and emits real audit chain rows, decision events, flight timelines, and (in some scenarios) incidents.*

The packs live under `demos/` in the repo. They are deliberately not bundled into deploy tarballs — they're operational fixtures, not product code.

| Pack | Scenarios | Duration | Agent name | Tools |
|---|---|---|---|---|
| `db_copilot` | 5 | ~11s | `db-copilot-demo` | `db.query`, `db.execute`, `execute_agent` |
| `devops_agent` | 9 | ~14s | `devops-agent-demo` | 23 K8s tools (`k8s.delete_pod`, `k8s.scale`, `k8s.rbac.grant`, …) |
| `support_agent` | 7 | ~21s | `support-agent-demo` | 9 CRM / ticketing tools (`crm.get_customer`, `crm.bulk_export`, `email.send`, …) |

Together they generate ~200 audit rows, ~50 decision history entries, ~50 flight timelines, and exercise every signal class (PII density, destructive DDL, K8s detectors, cross-tenant, OPA hard-deny, rate-limit burst).

## What each pack proves

### `db_copilot` — SQL governance

1. **Safe SELECT** → `allow`, risk≈0.0. Establishes the baseline cost of a permitted call (~70ms p95 through the full pipeline).
2. **Bulk `SELECT *`** → `monitor`, risk≈0.25. Behaviour-drift signal fires without blocking.
3. **PII exfiltration query** → `throttle`, risk≈0.60. The PII density signal counts SSN + credit_card columns × row estimate.
4. **`DROP TABLE`** → `kill`, risk=0.95. Destructive-DDL signal hard-denies.
5. **Kill switch active** → every subsequent call returns 403 with `error: "kill_switch_engaged"`.

### `devops_agent` — Kubernetes governance

9 scenarios across read / scale / delete / RBAC mutation, ending with:

- **Blast Radius** visualisation via the identity graph (1 cluster-admin path, 8 reachable nodes).
- **Autonomy contract** enforcement: `k8s.delete.*` requires human approval after the first destructive op.
- **Runaway automation defense**: a 10-op delete storm tripping `pod_deletion_storm` + `destructive_deletion_loop` detectors, ending in HTTP 429.
- **Kill switch persistence**: engage → simulated Redis FLUSHDB → still engaged because the `kill_switches` Postgres table is the durable source.

### `support_agent` — CRM / PII governance

7 scenarios:

1. Ticket lookup → `allow`.
2. Single-customer PII fetch → `monitor`.
3. Cross-tenant data access → hard `deny` via the tenant-isolation invariant.
4. Bulk PII export → `deny` via the PII density signal (>5 sensitive columns × >100 rows).
5. Email exfiltration → `deny` via the OPA hard-deny policy on `email.send` outside `allowed_email_domain`.
6. Runaway burst → HTTP 429 after exceeding the 30/min tenant quota.
7. Cryptographic receipt + chain verify on the day's emitted rows.

## How to run them

The packs need direct access to the gateway, identity, identity_graph, and autonomy services. From a laptop, only the gateway is publicly reachable; identity/graph/autonomy bind to internal `:8000` only. The standard recipe is therefore a throwaway `python:3.11-slim` container joined to the EC2's `infra_default` docker network.

### Pre-requisites

1. The dev environment is up: `curl -fsS https://dev.aegisagent.in/system/health` returns `healthy: 12 / total: 12`.
2. The repo's `demos/` directory has been uploaded to S3 (it isn't in the production tarball — `aws s3 cp demos.tar.gz s3://acp-dev-backups-628478/demos/`).
3. The dev admin credentials work: `admin@acp.local` is accepted by the gateway as of 2026-06-01 (`.local` TLD was rejected before that — see `services/gateway/routers/auth.py`).

### One-shot runner

```bash
docker run --rm --network infra_default \
  -v /opt/aegis:/repo -w /repo \
  -e ACP_GATEWAY_URL=http://gateway:8000 \
  -e ACP_IDENTITY_URL=http://identity:8000 \
  -e ACP_GRAPH_URL=http://identity_graph:8000 \
  -e ACP_AUTONOMY_URL=http://autonomy:8000 \
  -e INTERNAL_SECRET=<from Secrets Manager: acp-dev/internal_secret> \
  -e ACP_TENANT_ID=00000000-0000-0000-0000-000000000001 \
  -e ACP_ADMIN_EMAIL=admin@acp.local \
  -e ACP_ADMIN_PASSWORD=<from onboarding> \
  -e DEMO_PG_DSN=postgresql+asyncpg://postgres:<RDS pwd>@acp-postgres-dev.cz0qqg60keaj.ap-south-1.rds.amazonaws.com:5432/acp_demo \
  -e DEMO_ADMIN_PG_DSN=postgresql+asyncpg://postgres:<RDS pwd>@acp-postgres-dev.cz0qqg60keaj.ap-south-1.rds.amazonaws.com:5432/postgres \
  python:3.11-slim bash -c \
    "pip install -q httpx sqlalchemy asyncpg && python demos/run_all_demos.py"
```

Two gotchas to know up front:

- **Internal services listen on `:8000`** for every container — not 8002/8013/8015 like the local-dev defaults inside `setup_demo.py`. Override every `ACP_*_URL` env var.
- **The tenant header value is `tenants.tenant_id`, not the row primary key.** For the seeded dev tenant: `00000000-0000-0000-0000-000000000001`, not the UUID `d77eecb9-…`.

The runner does setup + scripted execution for all three packs. Setup is idempotent except for agent name uniqueness — re-runs after a partial failure require cleaning up the previous `db-copilot-demo` / `devops-agent-demo` / `support-agent-demo` rows. The `agents` table has RLS enabled and rejects `DELETE` from the `postgres` superuser without an explicit `ALTER TABLE … DISABLE ROW LEVEL SECURITY` first.

## Verifying the result

After a successful run, the UI populates as follows:

| UI surface | Expected after one run |
|---|---|
| Agents | 3 entries: `db-copilot-demo`, `devops-agent-demo`, `support-agent-demo` |
| Observability / Audit | ~200 calls, ~2 hard denies, ~200 low-risk + ~1 medium |
| Decision History | 50+ recent decisions across all 3 agents |
| Flight Recorder | 50+ closed timelines (each `/execute` opens + closes one) |
| Transparency | One signed Merkle root for today |
| Incidents | Empty unless the operator has seeded fixtures separately |

The Incidents page emptiness is by design — the demo scripts never trip the incident-creation rules. To populate it for screenshots, seed five representative rows directly into `acp_api.incidents` (one per severity / lifecycle state).

## Memory-safe usage

The packs are read-or-write against the live dev environment. They are not isolated. Running them:

- Mutates the registry (`agents` rows), the autonomy service (contracts), the identity_graph (nodes / edges), and the tenant rpm_limit (support pack sets 30/min for scenario 6).
- Does **not** mutate production-style billing — usage events flow through but no Stripe webhook is wired in dev.
- Does **not** leave fixtures behind beyond the agent rows and audit/decision history. To reset, drop the three `*-demo` rows from `acp_registry.agents`.

## Next

- [Quickstart](quickstart.md) — manual curl walkthrough against the same dev environment
- [60-second tour](60-second-tour.md) — UI walkthrough after a pack has populated the dashboards
- [Decision service](../services/decision.md) — the signal stack the packs exercise
- [Kill Switch](../security/kill-switch.md) — the lever the DevOps pack flips at scenario 8
