# Services Map

*The 16 backend services in `services/`, plus two cross-cutting documentation pages (Billing — a cross-service flow; Intelligence — an embedded module in `sdk/intelligence/`), grouped by role in the request lifecycle.*

## Hot path — on the request path of every `/execute`

| Service | Folder | Port | Database | Page |
|---|---|---|---|---|
| Gateway | `services/gateway/` | 8000 | none (Redis only) | [Gateway](gateway.md) |
| Identity | `services/identity/` | 8002 | `acp_identity` | [Identity](identity.md) |
| Registry | `services/registry/` | 8001 | `acp_registry` | [Registry](registry.md) |
| Policy | `services/policy/` | 8003 | none (OPA-local) | [Policy](policy.md) |
| Decision | `services/decision/` | 8004 | none (Redis only) | [Decision](decision.md) |

The gateway runs the 11-stage middleware pipeline; the other four are consulted inline at specific stages.

## Trust layer — runtime governance plus durable record

| Service | Folder | Port | Database | Page |
|---|---|---|---|---|
| Behavior | `services/behavior/` | 8005 | `acp_behavior` | [Behavior](behavior.md) |
| Audit | `services/audit/` | 8006 | `acp_audit` | [Audit](audit.md) |
| Autonomy | `services/autonomy/` | 8015 | `acp_autonomy` | [Autonomy](autonomy.md) |
| Identity Graph | `services/identity_graph/` | 8013 | `acp_identity_graph` | [Identity Graph](identity-graph.md) |
| Flight Recorder | `services/flight_recorder/` | 8012 | `acp_flight_recorder` | [Flight Recorder](flight-recorder.md) |
| Forensics | `services/forensics/` | 8011 | reads `acp_audit`, `acp_identity_graph`, `acp_flight_recorder` | [Forensics](forensics.md) |

Behavior and Autonomy are inline at stages 5 and 7; the other four are post-decision (Audit at stage 10, Flight Recorder fire-and-forget, Identity Graph fire-and-forget, Forensics is read-only).

## Operations and intelligence

| Service | Folder | Port | Database | Page |
|---|---|---|---|---|
| Usage | `services/usage/` | 8007 | `acp_usage` | [Usage](usage.md) |
| API | `services/api/` | 8010 | `acp_api` | [API](api.md) |
| Insight | `services/insight/` | 8014 | reads `acp_audit` | [Insight](insight.md) |
| Learning | `services/learning/` | 8016 | reads `acp_behavior` | [Learning](learning.md) |
| MCP Server | `services/mcp_server/` | stdio | none | — (stdio surface, no HTTP) |

Billing is a cross-service flow (`audit` → outbox → `usage` → optional
Stripe webhook in `api`), not a separate service. Intelligence-style
cross-agent correlation lives inside the `learning` service.

`insight_worker` runs alongside Insight but is not addressable on a port.

## Counts at a glance

- **Total backend services**: 16 in `services/` (gateway, identity, registry, policy, decision, behavior, audit, usage, api, forensics, flight_recorder, identity_graph, insight, autonomy, learning, mcp_server).
- **Plus 2 cross-cutting docs pages**: Billing (a cross-service flow) and Intelligence (an embedded module in `sdk/intelligence/`).
- **Standalone HTTP FastAPI services**: 15 — every service in `services/` except `mcp_server` (stdio).
- **Postgres logical databases**: 11 — one per data-owning service plus the `acp` bootstrap database.
- **Services with no Postgres of their own**: 5 — Gateway, Decision, Policy, Forensics (read-only), MCP Server (stateless).

## Reading order

A new engineer should read in this order:

1. [Gateway](gateway.md) — the entry point.
2. [Audit](audit.md) — the durable record.
3. [Decision](decision.md) — the signal combiner.
4. [Identity](identity.md) — the auth source of truth.
5. [Policy](policy.md) — the OPA Rego host.
6. [Registry](registry.md) — the agent and tool grants.
7. [Behavior](behavior.md) — the firewall.
8. [Autonomy](autonomy.md) — the multi-agent contracts.

After those eight, the remaining ones are smaller and self-contained — read them as needed.

## Cross-references

- The full architecture is in [System Overview](../architecture/system-overview.md).
- The 11-stage pipeline detail is in [Gateway Pipeline](../architecture/10-stage-pipeline.md).
- A worked end-to-end example is in [Flow of a Decision](../architecture/flow-of-a-decision.md).
- The data model across services is in [Data Model](../architecture/data-model.md).
