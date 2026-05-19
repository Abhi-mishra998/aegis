from __future__ import annotations

from collections.abc import Awaitable

import structlog

logger = structlog.get_logger(__name__)


async def safe_bg(coro: Awaitable[object]) -> None:
    """Swallow and log exceptions from fire-and-forget coroutines.

    Prevents 'Task exception was never retrieved' noise when used with
    asyncio.create_task(). Every caller site stays identical — just wrap
    the coroutine: asyncio.create_task(safe_bg(my_coro())).
    """
    try:
        await coro
    except Exception as exc:
        logger.warning("background_task_failed", error=str(exc))
