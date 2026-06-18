"""
D4 regression: concurrent get_engine() against asyncpg + pgbouncer must not
raise DuplicatePreparedStatementError on cold start. Integration-marked, so
skipped in default CI; runs when Postgres + pgbouncer are reachable.
"""
from __future__ import annotations

import asyncio
import os
import socket
from urllib.parse import urlparse

import pytest
from sqlalchemy import text


def _db_reachable() -> tuple[bool, str]:
    """Return (reachable, reason); reason filled only when not reachable."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False, "DATABASE_URL not set"
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        socket.create_connection((host, port), timeout=2).close()
    except OSError as exc:
        return False, f"cannot reach {host}:{port}: {exc}"
    return True, ""


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_get_engine_no_prepared_statement_race() -> None:
    """20 concurrent SELECT 1s through get_engine() must all succeed.

    Regression for the asyncpg+pgbouncer cold-start race where SQLAlchemy's
    implicit prepared-statement names collide across connections that share a
    pgbouncer session pool (DuplicatePreparedStatementError).
    """
    reachable, reason = _db_reachable()
    if not reachable:
        pytest.skip(reason)

    import asyncpg

    from sdk.common.db import get_engine

    async def _probe() -> int:
        engine = get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar_one()

    try:
        results = await asyncio.gather(*[_probe() for _ in range(20)])
    except asyncpg.exceptions.DuplicatePreparedStatementError as exc:  # pragma: no cover - D4 regression
        pytest.fail(f"D4 regression: prepared-statement race resurfaced: {exc}")

    assert results == [1] * 20, f"expected 20 successful SELECT 1s, got {results}"
