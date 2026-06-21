import asyncio
import json
import time

import httpx
import structlog

from sdk.common.config import settings
from sdk.common.redis import get_redis_client

logger = structlog.get_logger(__name__)

# Constants for minimal, production-ready implementation
_TIMEOUT = httpx.Timeout(5.0)
_OPA_URL = f"{settings.OPA_URL.rstrip('/')}/v1/data/acp/v1/agent"

# Sprint 2 — Lazy Redis client for SSE policy_decision publishes. The
# OPAClient itself is a singleton; we share one redis connection across
# all check_policy calls. Best-effort: failures must NOT break policy
# evaluation, which is the hot path of /execute.
_redis_client = None


def _get_publish_redis():
    """Return a cached Redis client for SSE publishing; None on failure."""
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = get_redis_client(settings.REDIS_URL, decode_responses=False)
        except Exception as exc:
            logger.warning("opa_redis_init_failed", error=str(exc))
            _redis_client = False  # sentinel: do not retry every call
    return _redis_client if _redis_client else None


class OPAClient:
    """
    Minimal async HTTP client for Open Policy Agent.
    Focuses on correctness and simplicity as per requirements.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def wait_for_ready(self, max_retries: int = 10, initial_delay: float = 1.0) -> bool:
        """Readiness check with exponential backoff retry."""
        delay = initial_delay
        for i in range(max_retries):
            try:
                client = self._get_client()
                resp = await client.get(f"{settings.OPA_URL.rstrip('/')}/health")
                if resp.status_code == 200:
                    logger.info("opa_ready", attempt=i + 1)
                    return True
            except Exception as exc:
                logger.warning("opa_not_ready_retry", attempt=i + 1, error=str(exc))

            if i < max_retries - 1:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 10.0)  # Cap delay at 10s

        return False

    async def check_policy(self, input_data: dict) -> tuple[bool, str, float]:
        """
        Evaluate policy against OPA.
        Returns (allow: bool, reason: str, risk_adjustment: float).

        Failure Handling:
        If OPA is unreachable or returns non-200 -> DENY (system_unavailable)
        """
        client = self._get_client()
        body = {"input": input_data}

        start_time = time.perf_counter()
        try:
            response = await client.post(_OPA_URL, json=body)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            if response.status_code != 200:
                logger.error("opa_error", status_code=response.status_code, reason="system_unavailable")
                if settings.OPA_FAIL_MODE == "open":
                    return True, "Policy engine error: fail_open", 0.0
                return False, "Policy engine error: system_unavailable", 0.0

            result = response.json().get("result", {})

            if result is None:
                logger.error("opa_policy_missing", url=_OPA_URL, reason="system_unavailable")
                if settings.OPA_FAIL_MODE == "open":
                    return True, "Policy not found: fail_open", 0.0
                return False, "Policy not found: system_unavailable", 0.0

            # Support new 'main' structure and backward-compatible root fields
            main = result.get("main", {})
            allowed = bool(main.get("allow", result.get("allow", False)))
            reason = str(main.get("reason", result.get("reason", "No reason provided by OPA")))
            adjustment = float(main.get("risk_adjustment", result.get("risk_adjustment", 0.0)))

            logger.info(
                "policy_decision",
                allowed=allowed,
                reason=reason,
                risk_adjustment=adjustment,
                duration_ms=duration_ms
            )

            # Sprint 2 — best-effort SSE publish so the dashboard LiveFeed
            # can render policy decisions in near-real-time. Keeps a single
            # cached Redis client across calls. Failures are swallowed — a
            # publish error must never break the policy hot path.
            tenant_id = str(input_data.get("tenant_id", "") or "")
            if tenant_id:
                agent_ctx = input_data.get("agent") or {}
                agent_id = str(agent_ctx.get("id", "") or "") if isinstance(agent_ctx, dict) else ""
                tool = str(input_data.get("tool", "") or "")
                redis_client = _get_publish_redis()
                if redis_client is not None:
                    # N2 (2026-06-21) — payload carries top-level
                    # ``tenant_id`` so the SSE generator can verify a
                    # cross-tenant publish never leaks. The channel name is
                    # not a trust boundary on its own.
                    payload = json.dumps({
                        "tenant_id": tenant_id,
                        "type": "policy_decision",
                        "data": {
                            "agent_id": agent_id or None,
                            "action": tool or None,
                            "allowed": allowed,
                            "reason": reason,
                            "reasons": [reason] if reason else [],
                            "risk_adjustment": adjustment,
                        },
                        "ts": int(time.time()),
                    })
                    try:
                        await redis_client.publish(
                            f"acp:events:{tenant_id}", payload,
                        )
                        if agent_id:
                            await redis_client.publish(
                                f"acp:events:{tenant_id}:{agent_id}", payload,
                            )
                    except Exception as exc:
                        logger.warning(
                            "policy_decision_publish_failed",
                            error=str(exc),
                        )

            return allowed, reason, adjustment

        except Exception as exc:
            logger.error("opa_unreachable", error=str(exc), reason="system_unavailable")
            if settings.OPA_FAIL_MODE == "open":
                logger.warning("opa_fail_open_mode_active")
                return True, "Policy engine unreachable: fail_open", 0.0
            return False, "Policy engine unreachable: system_unavailable", 0.0

    async def health(self) -> bool:
        return await self.wait_for_ready()

    async def evaluate(self, input_data: dict, version: str = "v1") -> tuple[bool, str]:
        """Legacy wrapper for backward compatibility."""
        allowed, reason, _ = await self.check_policy(input_data)
        return allowed, reason


opa_client = OPAClient()

