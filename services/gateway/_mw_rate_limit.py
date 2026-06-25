"""
_RateLimitMixin — rate limiting and quota helpers extracted from
SecurityMiddleware.  All methods use ``self.redis`` and ``self.limiter``
which are initialised by SecurityMiddleware.__init__ at runtime.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse
from jose import JWTError, jwt

from prometheus_client import Counter

from sdk.common.config import settings
from sdk.utils import RATE_LIMIT_EXCEEDED_TOTAL

logger = structlog.get_logger(__name__)

# Sprint 25 A9 — Redis-failure visibility in the rate-limiter fail-open paths.
# All 3 sites previously did `except Exception: pass` silently; now they
# emit a structured log AND bump this counter so SOC can alert when
# Redis becomes flaky enough to disable rate-limiting at scale.
RATE_LIMITER_REDIS_FAILURE_TOTAL = Counter(
    "acp_rate_limiter_redis_failure_total",
    "Redis call failed inside a rate-limiter fail-open path",
    ["site"],
)

_GLOBAL_RATE_LIMIT = settings.GLOBAL_RATE_LIMIT
_IP_RATE_LIMIT = settings.IP_RATE_LIMIT
_TENANT_RATE_LIMIT = settings.TENANT_RATE_LIMIT
_AGENT_RATE_LIMIT = settings.AGENT_RATE_LIMIT
_TOKEN_RATE_LIMIT = settings.TOKEN_RATE_LIMIT
_RATE_WINDOW = 60  # seconds


class _RateLimitMixin:
    async def _check_rate_limits(
        self,
        tenant_id_str: str,
        agent_id: uuid.UUID,
        jti: str | None,
        tier: str,
        rpm_limit: int = 0,
    ) -> None:
        """
        Check tenant, agent, and token rate limits.
        rpm_limit: real per-minute limit from the Tenant record (0 = use config default).
        """
        effective_tenant_limit = rpm_limit if rpm_limit > 0 else _TENANT_RATE_LIMIT
        if not await self.limiter.check_limit(
            f"acp:ratelimit:tenant:{tenant_id_str}",
            effective_tenant_limit,
            _RATE_WINDOW,
            tier=tier,
        ):
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="tenant", tier=tier).inc()
            raise HTTPException(status_code=429, detail="Tenant rate limit exceeded")

        if not await self.limiter.check_limit(
            f"acp:ratelimit:agent:{tenant_id_str}:{str(agent_id)}",
            _AGENT_RATE_LIMIT,
            _RATE_WINDOW,
            tier=tier,
            check_pool=False,
        ):
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="agent", tier=tier).inc()
            raise HTTPException(status_code=429, detail="Agent rate limit exceeded")

        if not await self.limiter.check_token_limit(
            jti, str(agent_id), _TOKEN_RATE_LIMIT, _RATE_WINDOW, tier=tier, check_pool=False
        ):
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="token", tier=tier).inc()
            raise HTTPException(status_code=429, detail="Token rate limit exceeded")

    async def _check_early_defense(self, client_ip: str) -> Response | None:
        """Global and IP-based rate limiting."""
        if not await self.limiter.check_limit(
            "acp:ratelimit:global", _GLOBAL_RATE_LIMIT, _RATE_WINDOW, check_pool=False
        ):
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="global", tier="none").inc()
            return self._deny("System-wide rate limit reached", 429)

        if not await self.limiter.check_limit(
            f"acp:ratelimit:ip:{client_ip}", _IP_RATE_LIMIT, _RATE_WINDOW, check_pool=False
        ):
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="ip", tier="none").inc()
            return self._deny("IP-based rate limit exceeded", 429)
        return None

    # QA-MW-FIX (2026-06-24) — burst-on-401 limiter (closes P2-5).
    # The pentest report showed 50 anonymous requests to /workspace/me
    # from a single IP all returned 401 with zero 429. That meant a
    # credential-stuffer could pound any auth-gated endpoint at full
    # speed from one IP forever. WAF runs at L4 and doesn't see the
    # app-layer 401, so this limiter must live here. We keep a 60-second
    # rolling INCR per real-client IP and 429 the IP after
    # _AUTH_FAIL_BURST_LIMIT consecutive failures. Counter clears on
    # the next successful auth FROM THE SAME REAL CLIENT IP only — a
    # different legitimate user from a different IP cannot reset an
    # attacker's counter.
    _AUTH_FAIL_BURST_WINDOW = 60   # seconds
    _AUTH_FAIL_BURST_LIMIT  = 25   # 401s per IP per window

    @staticmethod
    def _real_client_ip(request: Any) -> str:
        """Return the originating-client IP, trusting X-Forwarded-For
        when the immediate upstream is a known proxy (nginx / ALB).

        Why this exists: ``request.client.host`` returns the IP of the
        last hop (nginx in our stack). Every customer request appears
        to come from the same nginx IP, so per-IP limiters fire either
        never or for everyone. The QA pre-launch audit found 50 anon
        bursts on /workspace/me producing 50× 401 + zero 429 for
        exactly this reason. Reading the first entry of XFF gives the
        attacker's real source IP.

        Spoofing note: a malicious client can pre-set X-Forwarded-For.
        We only trust the header when the immediate upstream is a
        loopback / private-range IP (nginx-on-the-host or an internal
        ALB target). Public hits from a direct attacker land on
        ``request.client.host`` straight away — no XFF trust at all.
        """
        try:
            host = (request.client.host if request.client else "") or ""
        except Exception:
            host = ""
        # Trust XFF only when the immediate upstream is internal.
        trust = (
            host.startswith("127.")
            or host.startswith("10.")
            or host.startswith("172.")
            or host.startswith("192.168.")
            or host in ("", "::1", "localhost", "unknown")
        )
        if trust:
            xff = request.headers.get("X-Forwarded-For", "")
            if xff:
                # First entry = originating client, per nginx's
                # `proxy_add_x_forwarded_for` semantics.
                first = xff.split(",")[0].strip()
                if first:
                    return first
        return host or "unknown"

    @staticmethod
    def _has_valid_acp_signature(request: Request) -> bool:
        """Local-only HS256 signature check on the ``Authorization`` bearer.

        Returns True iff the bearer parses + verifies against the local
        ``JWT_SECRET_KEY`` with the configured algorithm. Expiry is NOT
        enforced here — a marginally-expired bearer still bypasses the
        anonymous-burst gate, and the downstream auth path will still
        reject it cleanly (and re-increment the counter via 401).

        Why this exists: the pentest matrix on 2026-06-25 surfaced a
        cascade where 26 anonymous 401 probes from one IP tripped the
        burst gate, and the next *legitimate* probe with a valid OWNER
        JWT (E13: ``/audit/export?days=1``) returned 429 even though
        the bearer was sound. A security tester sweeping endpoints from
        a single laptop should not lock themselves out of their own
        authenticated probes. The fix below makes the gate strictly
        an *anonymous-or-invalid-bearer* gate — exactly what it was
        always meant to be.
        """
        auth = request.headers.get("Authorization", "")
        if not auth or not auth.lower().startswith("bearer "):
            return False
        token = auth[7:].strip()
        if not token or token.count(".") != 2:
            return False
        try:
            jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False, "verify_aud": False},
            )
            return True
        except JWTError:
            return False
        except Exception:
            return False

    async def _check_auth_failure_burst(
        self,
        client_ip: str,
        request: Request | None = None,
    ) -> Response | None:
        """Return 429 if this IP has burned through the auth-failure budget.

        Runs BEFORE auth on every non-skiplisted request. Read-only — does
        not increment; only the actual 401 path increments (via
        ``_record_auth_failure``). Fail-open on Redis errors so an outage
        in the rate-limit Redis cannot lock customers out.

        A request that carries a locally-verifiable HS256 ACP bearer
        bypasses the gate entirely — see ``_has_valid_acp_signature``.
        """
        if not client_ip or client_ip == "unknown":
            return None
        # Locally-verifiable bearers bypass the anonymous-burst gate.
        if request is not None and self._has_valid_acp_signature(request):
            return None
        key = f"acp:ratelimit:auth_fail:{client_ip}"
        try:
            n = await self.redis.get(key)
        except Exception:
            return None
        if n is None:
            return None
        try:
            count = int(n)
        except (TypeError, ValueError):
            return None
        if count > self._AUTH_FAIL_BURST_LIMIT:
            RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="auth_fail_ip", tier="none").inc()
            return self._deny(
                f"Too many authentication failures from this IP; retry in "
                f"{self._AUTH_FAIL_BURST_WINDOW}s",
                429,
            )
        return None

    async def _record_auth_failure(self, client_ip: str) -> None:
        """Increment the per-IP 401 counter (60s rolling)."""
        if not client_ip or client_ip == "unknown":
            return
        key = f"acp:ratelimit:auth_fail:{client_ip}"
        try:
            cnt = await self.redis.incr(key)
            if cnt == 1:
                await self.redis.expire(key, self._AUTH_FAIL_BURST_WINDOW)
        except Exception as exc:
            RATE_LIMITER_REDIS_FAILURE_TOTAL.labels(site="record_auth_failure").inc()
            logger.warning("ratelimit_record_auth_failure_failed", client_ip=client_ip, error=str(exc))
            pass  # fail-open

    async def _clear_auth_failure_counter(self, client_ip: str) -> None:
        """Successful auth resets the burst counter — a legitimate user
        who mistypes once shouldn't be locked out of their own session."""
        if not client_ip or client_ip == "unknown":
            return
        try:
            await self.redis.delete(f"acp:ratelimit:auth_fail:{client_ip}")
        except Exception as exc:
            RATE_LIMITER_REDIS_FAILURE_TOTAL.labels(site="clear_auth_failure").inc()
            logger.warning("ratelimit_clear_auth_failure_failed", client_ip=client_ip, error=str(exc))
            pass  # fail-open

    async def _check_execute_sliding_window(
        self,
        tenant_id_str: str,
        jti: str | None,
    ) -> Response | None:
        """
        10-second sliding window applied to POST /execute only.
        Catches sequential burst that the 60-second token-bucket misses
        because 10 000 rpm limits are too coarse for sequential probing.
        Limits: 30 req/10 s per tenant (3 rps) + 15 req/10 s per JTI.
        Fail-open on Redis errors — a cache blip must not drop real traffic.
        """
        _WINDOW    = 10
        _T_LIMIT   = 30
        _JTI_LIMIT = 15
        try:
            t_key = f"acp:ratelimit:execute_sw:tenant:{tenant_id_str}"
            t_cnt = await self.redis.incr(t_key)
            if t_cnt == 1:
                await self.redis.expire(t_key, _WINDOW)
            if t_cnt > _T_LIMIT:
                RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="execute_sw_tenant", tier="none").inc()
                return self._deny("Rate limit exceeded: request volume too high", 429)

            if jti:
                j_key = f"acp:ratelimit:execute_sw:token:{jti}"
                j_cnt = await self.redis.incr(j_key)
                if j_cnt == 1:
                    await self.redis.expire(j_key, _WINDOW)
                if j_cnt > _JTI_LIMIT:
                    RATE_LIMIT_EXCEEDED_TOTAL.labels(layer="execute_sw_token", tier="none").inc()
                    return self._deny("Rate limit exceeded: token request volume too high", 429)
        except Exception as exc:
            RATE_LIMITER_REDIS_FAILURE_TOTAL.labels(site="execute_sliding_window").inc()
            logger.warning("ratelimit_execute_sw_failed", error=str(exc))
            pass  # fail-open
        return None

    # Read-only endpoints that stay accessible even when a tenant has
    # exhausted its monthly cap — so customers can pull their audit /
    # transparency / billing data to investigate. The list is a *prefix*
    # check; any GET request is read-only by HTTP semantics so we
    # allow those independently of the prefix list.
    _MONTHLY_READONLY_POST_PREFIXES = (
        "/receipts/verify",
        "/transparency/verify-root",
        "/internal/reconciliation-report",
        "/auth/logout",
        "/auth/introspect",
    )

    @staticmethod
    def _is_readonly_for_monthly_cap(request: Request) -> bool:
        if request.method.upper() == "GET":
            return True
        path = request.url.path
        return any(path.startswith(p) for p in _RateLimitMixin._MONTHLY_READONLY_POST_PREFIXES)

    async def _enforce_tenant_quota(
        self,
        request: Request,
        t_id_str: str,
        agent_id: uuid.UUID,
        request_id: str,
    ) -> JSONResponse | None:
        """Run the three-layer per-tenant quota check; return None when
        the request may proceed, or a fully-formed 429 `JSONResponse`
        otherwise (audit row already emitted)."""
        limits = getattr(request.state, "quota_limits", None)
        if not limits:
            # Tenant metadata didn't resolve — let the request through
            # so downstream auth/security can produce the right error.
            return None

        from sdk.common.ratelimit import TenantQuotaLimiter
        limiter = TenantQuotaLimiter(self.redis)
        decision = await limiter.check(
            tenant_id=t_id_str,
            requests_per_second=int(limits["requests_per_second"]),
            burst=int(limits["burst"]),
            daily_cap=int(limits["daily_request_cap"]),
            monthly_cap=(int(limits["monthly_request_cap"])
                         if limits.get("monthly_request_cap") is not None else None),
        )
        if decision.allowed:
            return None

        # Monthly-cap-exceeded carries a read-only escape hatch so the
        # customer can still pull their audit data.
        if decision.limit_type == "monthly" and self._is_readonly_for_monthly_cap(request):
            return None

        # Quota denied: emit audit row + 429.
        try:
            from sdk.utils import TENANT_RATE_LIMITED_TOTAL
            TENANT_RATE_LIMITED_TOTAL.labels(
                tenant=t_id_str, limit_type=decision.limit_type or "unknown",
            ).inc()
        except Exception:
            pass

        # Audit synchronously — required by Sprint 3.2 acceptance.
        try:
            await self._log_audit(
                t_id_str, agent_id,
                "rate_limited",
                request.url.path,
                "deny",
                f"quota:{decision.limit_type}",
                request_id,
                {
                    "status":      429,
                    "limit_type":  decision.limit_type,
                    "reset_at":    decision.reset_at,
                    "usage":       decision.usage,
                },
            )
        except Exception as exc:
            logger.warning("rate_limit_audit_failed", error=str(exc), request_id=request_id)

        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(max(1, decision.retry_after_s))},
            content={
                "success":     False,
                "error":       "rate_limited",
                "limit_type":  decision.limit_type,
                "reset_at":    decision.reset_at,
                "meta": {
                    "code":          429,
                    "category":      "quota",
                    "request_id":    request_id,
                    "retry_after_s": decision.retry_after_s,
                },
            },
        )

    async def _enforce_inference_cost_cap(
        self,
        request: Request,
        t_id_str: str,
        agent_id: uuid.UUID,
        request_id: str,
        *,
        tokens: int,
    ) -> JSONResponse | None:
        """Sprint 3.5 — per-tenant + per-agent daily inference USD cap.

        Returns None when allowed; a 429 JSONResponse when blocked. Emits
        a synchronous audit row on block so the operator can grep
        ``action='inference_cost_cap_exceeded'`` and see exactly which
        request hit the cap and on which scope (tenant vs agent).
        """
        limits = getattr(request.state, "quota_limits", None) or {}
        tenant_cap = limits.get("daily_inference_cost_cap_usd")
        # Per-agent cap: hot-config Redis override. 0 / missing = no cap.
        agent_cap = 0.0
        try:
            raw = await self.redis.get(f"acp:agent_cost_cap:{agent_id}")
            if raw is not None:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("ascii", errors="replace")
                agent_cap = float(raw or 0)
        except Exception:
            agent_cap = 0.0
        if not tenant_cap and not agent_cap:
            return None

        from sdk.common.inference_cost import InferenceCostLimiter
        limiter = InferenceCostLimiter(self.redis)
        # Token estimate is approximate; assume worst-case 2x for output.
        estimated_usd = limiter.estimate_cost_usd(
            input_tokens=int(tokens), output_tokens=int(tokens) * 2,
        )
        decision = await limiter.check(
            tenant_id=t_id_str,
            agent_id=str(agent_id),
            estimated_usd=estimated_usd,
            tenant_cap_usd=float(tenant_cap or 0.0),
            agent_cap_usd=float(agent_cap or 0.0),
        )
        if decision.allowed:
            return None

        # Audit synchronously — the row is the SLI ops greps for.
        try:
            await self._log_audit(
                t_id_str, agent_id,
                "inference_cost_cap_exceeded",
                request.url.path,
                "deny",
                f"cost_cap:{decision.scope}",
                request_id,
                {
                    "status":           429,
                    "scope":            decision.scope,
                    "estimated_usd":    decision.estimated_usd,
                    "tenant_usd_used":  decision.tenant_usd_used,
                    "agent_usd_used":   decision.agent_usd_used,
                    "tenant_cap_usd":   decision.tenant_cap_usd,
                    "agent_cap_usd":    decision.agent_cap_usd,
                    "reset_at":         decision.reset_at,
                },
            )
        except Exception as exc:
            logger.warning("inference_cost_audit_failed", error=str(exc), request_id=request_id)

        # Retry tomorrow; the cap window resets at UTC midnight. The
        # Retry-After is approximate — clients should consult `reset_at`.
        retry_after_s = 60  # client backoff; not the actual reset
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after_s)},
            content={
                "success":     False,
                "error":       "inference_cost_cap_exceeded",
                "limit_type":  "inference_cost",
                "scope":       decision.scope,
                "reset_at":    decision.reset_at,
                "usage": {
                    "tenant_usd_used": decision.tenant_usd_used,
                    "agent_usd_used":  decision.agent_usd_used,
                    "tenant_cap_usd":  decision.tenant_cap_usd,
                    "agent_cap_usd":   decision.agent_cap_usd,
                },
                "meta": {
                    "code":          429,
                    "category":      "cost_cap",
                    "request_id":    request_id,
                    "retry_after_s": retry_after_s,
                },
            },
        )
