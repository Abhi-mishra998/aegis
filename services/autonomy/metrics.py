"""Prometheus metrics for the autonomy service.

Exports two series consumed by the customer-SLO Grafana board at
`infra/grafana-dashboards/customer-slo.json` panels 3 and 6:

- ``acp_autonomy_pending_approvals{tenant=...}`` — gauge of in-flight
  approval requests with no recorded override decision. Refreshed every
  30 s by the background loop in `gauge_refresh_loop` (kicked off by
  the autonomy service's lifespan in `main.py`).

- ``acp_autonomy_approval_resolution_seconds{tenant=...}`` — histogram
  of wall-clock seconds from violation detection to the human override.
  Observed inline in the `POST /overrides` handler (router.py) when an
  `approval` event is recorded against a known request_id.

Pending-approval definition. A "pending" row is an
``autonomy_contract_violations`` row whose ``request_id`` has no
matching ``human_override_events`` row, restricted to the trailing 24 h
window. The 24 h cap is intentional: an unresolved violation older than
that is almost certainly a deletion/cleanup artefact, not a real open
queue. This keeps the gauge bounded and avoids an unbounded join.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from prometheus_client import Gauge, Histogram
from sqlalchemy import func, select

from services.autonomy.models import AutonomyViolation, HumanOverrideEvent

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

PENDING_APPROVALS = Gauge(
    "acp_autonomy_pending_approvals",
    "In-flight approval requests with no recorded override decision (24h window).",
    ["tenant"],
)

# Buckets cover the realistic resolution range: a few seconds (instant CFO
# tap-through) to a day (overnight approver delay). 5s/30s/1m/5m/10m/30m/1h/4h/1d.
APPROVAL_RESOLUTION_SECONDS = Histogram(
    "acp_autonomy_approval_resolution_seconds",
    "Wall-clock seconds from violation detection to the human override event.",
    ["tenant"],
    buckets=(5, 30, 60, 300, 600, 1800, 3600, 14400, 86400),
)


# ---------------------------------------------------------------------------
# Gauge refresh — query + emit
# ---------------------------------------------------------------------------

_REFRESH_INTERVAL_SECONDS = 30


async def refresh_pending_gauge(session_factory) -> None:
    """One-shot query → set PENDING_APPROVALS per tenant.

    Uses a LEFT OUTER JOIN: violations with no matching override. Restricted
    to the trailing 24 h. Tenants with zero pending rows are *not* surfaced
    here; the gauge label remains at the last value Prometheus saw for them.
    To avoid a stuck non-zero label after the last violation resolves, we
    first set every previously-seen tenant to 0, then overwrite the live
    counts.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    stmt = (
        select(AutonomyViolation.tenant_id, func.count())
        .select_from(AutonomyViolation)
        .outerjoin(
            HumanOverrideEvent,
            (HumanOverrideEvent.request_id == AutonomyViolation.request_id)
            & (HumanOverrideEvent.tenant_id == AutonomyViolation.tenant_id),
        )
        .where(HumanOverrideEvent.id.is_(None))
        .where(AutonomyViolation.detected_at >= cutoff)
        .group_by(AutonomyViolation.tenant_id)
    )

    async with session_factory() as db:
        result = await db.execute(stmt)
        rows = result.all()

    # Reset every previously-tracked tenant label to 0 before applying the new
    # counts. Without this, a tenant whose last pending row just got resolved
    # would stay frozen at its old non-zero value until the next process
    # restart.
    for labels in list(PENDING_APPROVALS._metrics):
        PENDING_APPROVALS.labels(*labels).set(0)

    for tenant_id, count in rows:
        PENDING_APPROVALS.labels(tenant=str(tenant_id)).set(count)


async def gauge_refresh_loop(session_factory) -> None:
    """Background task — refresh `PENDING_APPROVALS` every 30 s.

    Lifespan: this coroutine runs for the lifetime of the autonomy
    service. It is cancelled (via task.cancel()) at lifespan shutdown.
    Exceptions on a single refresh are logged and the loop resumes on
    the next tick — the supervisor must not crash on a transient DB
    blip.
    """
    while True:
        try:
            await refresh_pending_gauge(session_factory)
        except asyncio.CancelledError:
            logger.info("autonomy_pending_gauge_loop_cancelled")
            raise
        except Exception as exc:
            logger.warning("autonomy_pending_gauge_refresh_failed", error=str(exc))
        await asyncio.sleep(_REFRESH_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Histogram observation — called inline from the /overrides handler
# ---------------------------------------------------------------------------


async def observe_approval_resolution(
    db,
    tenant_id,
    request_id: str | None,
    event_type: str,
) -> None:
    """Observe the time-to-resolve histogram when an approval is recorded.

    No-op when the override is not an "approval" event (e.g. an "override"
    event records a unilateral operator action with no preceding
    violation), or when the request_id has no matching violation row
    (e.g. the autonomy service was restarted between violation detection
    and the resolve, losing the in-memory request_id link).

    Failure here must NOT roll back the override that the caller has
    already committed. This function therefore swallows any DB exception
    rather than letting it propagate.
    """
    if event_type != "approval" or not request_id:
        return
    try:
        q = await db.execute(
            select(AutonomyViolation.detected_at)
            .where(
                AutonomyViolation.tenant_id == tenant_id,
                AutonomyViolation.request_id == request_id,
            )
            .order_by(AutonomyViolation.detected_at.desc())
            .limit(1)
        )
        detected_at = q.scalar_one_or_none()
    except Exception as exc:
        logger.warning("autonomy_resolution_lookup_failed", error=str(exc))
        return
    if detected_at is None:
        return
    delta = (datetime.now(UTC) - detected_at).total_seconds()
    if delta < 0:
        # Clock skew between the violation writer and this handler can
        # produce a tiny negative — histogram doesn't accept negatives.
        return
    APPROVAL_RESOLUTION_SECONDS.labels(tenant=str(tenant_id)).observe(delta)
