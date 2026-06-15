# Sprint 5 — Identity & Access Graph + Blast Radius

**Status:** in_progress
**Closes debt:** TD-7 — when an agent is quarantined, Aegis can't answer "what could it have reached if we hadn't fired?"
**Depends on:** Sprint 4 (incident IDs are the unit blast-radius is computed against).
**Blocks:** Sprint 6 (Auto-Remediation needs to know which resources to revoke when an incident escalates).

---

## Why this matters

The CISO sits down with the audit report. The audit shows: "We blocked agent X from
exfiltrating `customers.ssn`." The CISO asks: "If we *hadn't* blocked it, what
else could it have touched? Did this agent also have write access to the wire
transfer API? Could it have pulled the engineering vault?"

Today Aegis can't answer that. The block was decisive and correct. The
counterfactual — the *blast radius* — is unknowable from the audit log alone.

Sprint 5 is Aegis's Identity & Access Graph (IAG). For every agent we know:

- Which **roles** that agent holds (DB user, IAM user, vault policy, API
  scopes).
- Which **resources** each role has read / write / admin access to
  (tables, schemas, S3 prefixes, K8s namespaces, vault paths, HTTP
  destinations).
- The **transitive closure**: role → permission → resource.

When an incident closes, the Storyline now carries a `blast_radius` field:
*the set of resources this agent had access to but had NOT yet touched at
the time of block.* That's the number the CISO quotes in the audit report —
"we prevented access to 14 sensitive tables, 3 cloud accounts, and 1 wire
transfer endpoint."

EDR equivalents: CrowdStrike's "Identity Threat Detection," SentinelOne's
"Identity Surface," Splunk's "Risk-Based Alerting" all expose this. Aegis
ships an agent-native version because the identity graph for autonomous
agents (DB user + IAM + vault + API keys) is *richer* than the human-user
graph these tools were built for.

## Goal

For every agent, Aegis maintains:

1. A **forward graph** `agent → role → permission → resource`.
2. A **reverse graph** `resource → role → agent` (so when a resource is
   compromised we can answer "which agents could have written this?").
3. A **blast-radius computation** per `(agent, incident_id)` that returns:
   - `accessible_resources` — every resource the agent *could* reach by
     virtue of its current roles.
   - `touched_resources` — the subset that actually appeared in the
     incident's step list.
   - `untouched_resources` — `accessible \ touched`; the resources Aegis
     prevented the agent from reaching.
   - `criticality_score` — weighted sum of the resources by their
     sensitivity (PII tables score higher than dev sandboxes).

## Algorithm

### Ingestion

Roles + permissions are pulled from authoritative sources:

- **Database identities** — `acp_identity.roles`, `acp_identity.permissions`
  (already exist; the IAG just walks them).
- **External providers** — pluggable. Sprint 5 ships adapters for:
  - AWS IAM (via `boto3.client("iam").list_attached_user_policies`).
  - HashiCorp Vault (via the policies API).
  - PostgreSQL (via `pg_class` + `has_table_privilege`).
- The ingestion runs as a hourly background task. Each adapter implements
  `async def collect(self, tenant_id) -> list[IAGEdge]`.

### Storage

Redis-backed for hot reads (consistent with Sprint 4):

```
acp:iag:agent_roles:{tenant_id}:{agent_id}     SET    of role_ids
acp:iag:role_perms:{tenant_id}:{role_id}       SET    of perm_ids
acp:iag:perm_resources:{tenant_id}:{perm_id}   SET    of resource_ids
acp:iag:resource_meta:{tenant_id}:{resource_id} HASH  {kind, sensitivity, label}
```

24h TTL refreshed on every ingestion pass; deliberately not durable. The
authoritative copy is the upstream identity provider — Aegis is a cache
+ a query layer.

### Blast-radius query

Pure function:

```python
def compute_blast_radius(
    *, agent_id: str, touched_resources: set[str],
    agent_roles: set[str], role_perms: dict[str, set[str]],
    perm_resources: dict[str, set[str]],
    resource_meta: dict[str, ResourceMeta],
) -> BlastRadius:
```

Returns the dataclass with the four fields above. No I/O — easy to unit-test
against synthetic graphs.

## Success criteria

1. New module `services/security/iag/graph.py` — pure
   `compute_blast_radius()` + `ResourceMeta` + `BlastRadius` dataclasses.
2. New module `services/security/iag/store.py` — Redis read/write helpers.
3. New module `services/security/iag/ingestion.py` — base class +
   PostgreSQL adapter.
4. New router `services/gateway/routers/iag.py`:
   - `GET /iag/agents/{agent_id}` — accessible resources for an agent.
   - `GET /iag/incidents/{incident_id}/blast-radius` — augments the
     Sprint 4 storyline with the blast-radius slice.
5. Storyline middleware update: when a storyline closes (status →
   `blocked` or `quarantined`), the recorder calls
   `iag.attach_blast_radius(incident_id)` once so subsequent GETs include
   the field.
6. Unit tests:
   - `test_iag_graph_compute_blast_radius` — synthetic graph, expected
     `accessible/touched/untouched/criticality`.
   - `test_iag_store_round_trip` — writes via fake Redis, reads back.
   - `test_iag_ingestion_postgres_adapter` — fake DB cursor → expected
     edges.
7. Live: `GET /storylines/{INC-...}` on an incident from a quarantined
   agent returns `blast_radius` with `criticality_score > 0`.

## Non-goals

- **Cross-tenant joins.** Each tenant's IAG is sealed.
- **Real-time IAG updates.** Hourly ingestion is good enough — the IAG is
  a *snapshot for analytics*, not an ACL the request path consults.
- **AWS IAM + Vault adapters.** Sprint 5 ships the framework + the
  Postgres adapter. The IAM + Vault adapters land in Sprint 6 (auto-
  remediation has stronger product-market-fit for those).
- **UI surface.** JSON only.

## Files

**Added:**
- `services/security/iag/__init__.py`
- `services/security/iag/graph.py`
- `services/security/iag/store.py`
- `services/security/iag/ingestion.py`
- `services/gateway/routers/iag.py`
- `tests/security/test_iag_graph.py`
- `tests/security/test_iag_store.py`
- `tests/security/test_iag_ingestion.py`

**Touched:**
- `services/security/incidents/store.py` — attach `blast_radius` to
  Storyline output when the IAG has data for the incident.
- `services/gateway/main.py` — register `iag_router`.
- `services/gateway/middleware.py` — `/iag` path exemption.

## Rollout + rollback

- Deploy + restart `acp_gateway` on both ASG hosts.
- Storyline writes are unaffected; Sprint 4 keeps working without IAG.
- If the IAG ingestion misbehaves, set `ACP_IAG_INGESTION_ENABLED=0` and
  restart — Sprint 4 storyline endpoints still respond, blast-radius
  field is just absent.
