from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger(__name__)


# Every fail-open path that suppresses an exception should either wrap the
# coroutine in ``safe_bg`` (fire-and-forget) or call ``swallow_log`` in a
# ``try/except`` block (sync). Both routes increment the same counter
# labeled by ``event`` so ``rate(acp_exception_swallowed_total[5m])`` shows
# the hidden-error surface across services — a green request path that
# quietly swallows 400 exceptions per minute is now visible instead of
# invisible.
EXCEPTION_SWALLOWED_TOTAL = Counter(
    "acp_exception_swallowed_total",
    "Exceptions intentionally suppressed in fail-open paths.",
    ["event"],
)


def swallow_log(
    log: structlog.stdlib.BoundLogger,
    event: str,
    exc: BaseException,
    /,
    **fields: Any,
) -> None:
    """Log + count an intentionally-swallowed exception.

    Use inside ``try/except`` blocks that ARE fail-open by design (best-
    effort SSE publish, cache invalidation, SIEM forward, metric emit,
    optional integration lookup). NEVER use at a trust boundary — those
    must fail-closed by re-raising.

    ``event`` is the low-cardinality Prometheus label + the structlog event
    name. Callers pass structured context via ``**fields`` (tenant_id,
    request_id, etc.) so operators can grep the log for the specific
    failure mode.
    """
    EXCEPTION_SWALLOWED_TOTAL.labels(event=event).inc()
    log.warning(event, error=str(exc), exc_type=type(exc).__name__, **fields)


async def safe_bg(coro: Awaitable[object]) -> None:
    """Swallow and log exceptions from fire-and-forget coroutines.

    Prevents 'Task exception was never retrieved' noise when used with
    asyncio.create_task(). Every caller site stays identical — just wrap
    the coroutine: asyncio.create_task(safe_bg(my_coro())).
    """
    try:
        await coro
    except Exception as exc:
        swallow_log(logger, "background_task_failed", exc)
