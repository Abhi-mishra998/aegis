"""ATF v3.2 §4.2 SCIM reconciler — glues `sdk/common/scim_client` to
`services/policy/scim_agent.reconcile`.

Run modes:
  * On-demand via the `POST /scim/reconcile` endpoint (ops trigger).
  * Periodic — call `run_once()` from the identity lifespan on a
    scheduler (aiocron / apscheduler / plain asyncio loop).

The reconciler is READ-ONLY against SCIM; it emits a per-agent
`ReconcileAction` (OK / QUARANTINE / RESTORE) which the caller
persists via the registry service. This module NEVER mutates the
registry directly — separation of concerns keeps the reconciliation
loop safe to retry.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

import structlog

from sdk.common.config import settings
from sdk.common.scim_client import (
    ScimClient,
    ScimClientConfig,
    ScimStatus,
    ScimTransientError,
)
from services.policy.scim_agent import (
    AgentRecord,
    ReconcileResult,
    reconcile,
)

logger = structlog.get_logger(__name__)


def _is_enabled() -> bool:
    return bool(settings.SCIM_BASE_URL and settings.SCIM_BEARER_TOKEN)


# Bound on concurrent outbound SCIM requests. Customer SCIM directories
# routinely rate-limit at 100-1000 rps; a tenant with 10k agents +
# unbounded parallelism would trip the rate limit + risk connection
# pool exhaustion on our httpx client. 32 is conservative: it stays
# under most SCIM rate limits AND completes a 500-user directory in
# ~10 batches. Env-tunable per deployment.
import os as _os

_SCIM_CONCURRENCY = int(_os.getenv("SCIM_RECONCILE_CONCURRENCY", "32"))


async def run_once_async(agents: list[AgentRecord]) -> list[ReconcileResult]:
    """Async-native reconciliation for callers already inside an event
    loop (FastAPI endpoints, lifespan tasks). Same contract as run_once
    but awaits the SCIM lookup instead of shimming via asyncio.run.

    Concurrency is BOUNDED by `SCIM_RECONCILE_CONCURRENCY` — an
    unbounded burst would trip most customer SCIM rate limits and
    could exhaust our httpx connection pool. The semaphore limits
    concurrent lookups while still batching across the ref set.
    """
    if not _is_enabled():
        return []

    client = ScimClient(ScimClientConfig(
        base_url=settings.SCIM_BASE_URL,
        bearer_token=settings.SCIM_BEARER_TOKEN,
        timeout_seconds=settings.SCIM_RECONCILE_TIMEOUT_SECONDS,
    ))

    unique_refs = {a.human_responsible_scim_ref for a in agents if a.human_responsible_scim_ref}
    import asyncio
    sem = asyncio.Semaphore(_SCIM_CONCURRENCY)

    async def _bounded_lookup(ref: str) -> ScimStatus:
        async with sem:
            return await client.lookup_user(ref)

    tasks = {ref: asyncio.create_task(_bounded_lookup(ref)) for ref in unique_refs}
    statuses: dict[str, ScimStatus] = {}
    for ref, task in tasks.items():
        try:
            statuses[ref] = await task
        except ScimTransientError as exc:
            # Log + keep going; a single transient does not mass-quarantine.
            logger.warning("scim_lookup_transient", ref=ref, error=str(exc))
            # We omit the ref from `statuses` — the sync `reconcile` will
            # invoke the lookup callable which then raises the transient
            # error → OK result per the reconciler contract.

    def _prefetched(ref: str) -> ScimStatus:
        if ref not in statuses:
            raise ScimTransientError(f"prefetch_missing: {ref}")
        return statuses[ref]

    return reconcile(agents, _prefetched)


def summarize(results: list[ReconcileResult]) -> dict[str, Any]:
    counts = {"OK": 0, "QUARANTINE": 0, "RESTORE": 0}
    quarantined: list[dict[str, str]] = []
    restored: list[dict[str, str]] = []
    for r in results:
        counts[r.action] = counts.get(r.action, 0) + 1
        if r.action == "QUARANTINE":
            quarantined.append({"agent_id": r.agent_id, "reason": r.reason})
        elif r.action == "RESTORE":
            restored.append({"agent_id": r.agent_id, "reason": r.reason})
    return {
        "enabled":     _is_enabled(),
        "totals":      counts,
        "quarantine":  quarantined,
        "restore":     restored,
        "sample":      [asdict(r) for r in results[:10]],
    }
