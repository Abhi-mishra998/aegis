from __future__ import annotations

import os
from typing import Any

import structlog
from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

logger = structlog.get_logger(__name__)

# Docker-compose default — used when callers pass an empty/None URL so each
# call site does not need its own `os.environ.get("REDIS_URL", "redis://...")`
# fallback. Matches `infra/compose.yml` service name.
_DEFAULT_REDIS_URL = "redis://redis:6379/0"

_POOL_DEFAULTS: dict[str, Any] = {
    "max_connections": 50,
    "socket_timeout": 10.0,
    "socket_connect_timeout": 5.0,
    "retry_on_timeout": True,
    # health_check_interval removed: it sends a PING on every connection reuse after
    # idle period. On ElastiCache, this PING times out (socket_timeout × 3 internal
    # retries = ~30s wasted per check), causing audit and insight_worker to loop.
    # Actual operations (xreadgroup, xadd, get/set) reconnect automatically on failure.
}


def get_redis_client(
    url: str | None = None, decode_responses: bool = False, **kwargs: Any
) -> Redis | RedisCluster:
    """
    Returns a Redis or RedisCluster client based on the URL and environment.
    Automatic detection of cluster mode if URL scheme is 'redis' but host is a cluster.

    In this ACP setup, we use the REDIS_CLUSTER_ENABLED env var to force cluster mode.

    Falls back to ``REDIS_URL`` env var, then to the docker-compose default
    (``redis://redis:6379/0``) when ``url`` is empty or None. This lets callers
    drop their own ``os.environ.get("REDIS_URL", "...")`` boilerplate.
    """
    resolved_url = url or os.environ.get("REDIS_URL") or _DEFAULT_REDIS_URL
    is_cluster = os.getenv("REDIS_CLUSTER_ENABLED", "false").lower() == "true"
    pool_kwargs = {**_POOL_DEFAULTS, **kwargs}

    if is_cluster:
        logger.info("initializing_redis_cluster", url=resolved_url)
        return RedisCluster.from_url(
            resolved_url,
            decode_responses=decode_responses,
            skip_full_coverage_check=True,
            **pool_kwargs,
        )
    logger.info("initializing_redis_standalone", url=resolved_url)
    return Redis.from_url(resolved_url, decode_responses=decode_responses, **pool_kwargs)
