"""Sprint 5 — Identity & Access Graph ingestion.

Adapters pull authoritative role + permission + resource edges from an
upstream identity provider and write them to the Redis cache via
`store.upsert_*`. Each adapter implements the same `async def collect()`
shape so the orchestrator can run them in parallel.

Sprint 5 ships the framework + a PostgreSQL adapter that reads from the
existing `acp_identity.roles` + `acp_identity.permissions` tables. AWS
IAM + HashiCorp Vault adapters land in Sprint 6 (auto-remediation is the
stronger product motivator for those — once we can revoke an IAM policy
we want to know which agents lose what).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from . import store
from .graph import (
    KIND_TABLE,
    ResourceMeta,
    SENS_HIGH,
    SENS_LOW,
    SENS_MEDIUM,
)


@dataclass(frozen=True)
class IAGEdge:
    """One ingested (agent, role, permission, resource, meta) tuple.

    Adapters produce a stream of these. The orchestrator groups them into
    SET writes in Redis (`upsert_*`).
    """
    agent_id:    str
    role_id:     str
    perm_id:     str
    resource_id: str
    resource_meta: ResourceMeta


class BaseAdapter(abc.ABC):
    """Base class for every IAG ingestion adapter.

    Subclasses implement `collect(tenant_id)` returning a list of IAGEdge
    objects. The orchestrator handles batching them into Redis writes.
    """
    name: str = "base"

    @abc.abstractmethod
    async def collect(self, tenant_id: str) -> list[IAGEdge]:  # pragma: no cover
        ...


class PostgresAdapter(BaseAdapter):
    """Read agent → role → permission → resource from `acp_identity`.

    The Aegis identity DB already stores:
      - `users` (one row per agent identity)
      - `roles`
      - `role_permissions`
      - `permissions` (string `resource` like `customers.ssn`)

    The adapter joins them and tags each `permissions.resource` row as a
    table-kind IAG node. Sensitivity is heuristic-mapped from the
    permission name: anything containing `pii`, `ssn`, `customers` is
    HIGH; staging / dev paths drop to LOW; default MEDIUM. The Sprint 7
    threat-intel layer can override these by tenant.
    """
    name = "postgres"

    def __init__(self, db_pool: Any) -> None:
        self._pool = db_pool

    async def collect(self, tenant_id: str) -> list[IAGEdge]:
        sql = """
        SELECT
            u.id::text         AS agent_id,
            r.id::text         AS role_id,
            p.id::text         AS perm_id,
            p.resource         AS resource_id,
            p.action           AS action
        FROM acp_identity.users u
        JOIN acp_identity.user_roles ur     ON ur.user_id = u.id
        JOIN acp_identity.roles r           ON r.id = ur.role_id
        JOIN acp_identity.role_permissions rp ON rp.role_id = r.id
        JOIN acp_identity.permissions p     ON p.id = rp.permission_id
        WHERE u.tenant_id = $1
        """
        rows = []
        async with self._pool.acquire() as conn:
            async for row in conn.cursor(sql, tenant_id):
                rows.append(dict(row))
        edges: list[IAGEdge] = []
        for r in rows:
            res_id = str(r["resource_id"] or "")
            if not res_id:
                continue
            edges.append(IAGEdge(
                agent_id=str(r["agent_id"]),
                role_id=str(r["role_id"]),
                perm_id=str(r["perm_id"]),
                resource_id=res_id,
                resource_meta=ResourceMeta(
                    resource_id=res_id,
                    kind=KIND_TABLE,
                    label=res_id,
                    sensitivity=_infer_sensitivity(res_id, str(r.get("action") or "")),
                ),
            ))
        return edges


def _infer_sensitivity(resource_id: str, action: str) -> str:
    """Heuristic sensitivity classifier for the PG adapter.

    Tenants will eventually override this with their own taxonomy via the
    threat-intel layer (Sprint 7). For Sprint 5 the heuristic is good
    enough — labels stay correct often enough to make the criticality
    score meaningful, and incorrect labels are easy for the SOC to spot.
    """
    r = resource_id.lower()
    if any(s in r for s in ("ssn", "pii", "customer", "payment", "credit", "card", "secret", "vault")):
        return SENS_HIGH
    if any(s in r for s in ("staging", "dev", "sandbox", "test")):
        return SENS_LOW
    if action.lower() in ("delete", "drop", "truncate"):
        return SENS_HIGH
    return SENS_MEDIUM


# ---------------------------------------------------------------------------
# Orchestrator — groups edges and pushes them through the Redis writer.
# ---------------------------------------------------------------------------

async def ingest_all(
    redis: Any, tenant_id: str, adapters: list[BaseAdapter],
) -> int:
    """Run every adapter for one tenant, batch-upsert into Redis.

    Returns the total number of distinct resource nodes written so the
    caller can log a one-line ingestion summary.
    """
    edges: list[IAGEdge] = []
    for adapter in adapters:
        try:
            edges.extend(await adapter.collect(tenant_id))
        except Exception:
            # Adapter failures must not break the orchestrator; one busted
            # source (e.g. IAM throttled) shouldn't blank the whole graph.
            continue

    # Group into the four SET writes.
    agent_to_roles: dict[str, set[str]] = {}
    role_to_perms: dict[str, set[str]] = {}
    perm_to_resources: dict[str, set[str]] = {}
    resource_metas: dict[str, ResourceMeta] = {}

    for e in edges:
        agent_to_roles.setdefault(e.agent_id, set()).add(e.role_id)
        role_to_perms.setdefault(e.role_id, set()).add(e.perm_id)
        perm_to_resources.setdefault(e.perm_id, set()).add(e.resource_id)
        # First-write-wins for resource metadata — adapters should agree on
        # sensitivity, but if they don't we don't want to flip-flop. The
        # threat-intel override path (Sprint 7) is the right place to
        # resolve conflicts.
        resource_metas.setdefault(e.resource_id, e.resource_meta)

    for agent_id, roles in agent_to_roles.items():
        await store.upsert_agent_roles(redis, tenant_id, agent_id, roles)
    for role_id, perms in role_to_perms.items():
        await store.upsert_role_perms(redis, tenant_id, role_id, perms)
    for perm_id, resources in perm_to_resources.items():
        await store.upsert_perm_resources(redis, tenant_id, perm_id, resources)
    for meta in resource_metas.values():
        await store.upsert_resource_meta(redis, tenant_id, meta)
    await store.stamp_ingestion_done(redis, tenant_id)

    return len(resource_metas)
