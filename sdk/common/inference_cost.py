"""Sprint 3.5 — per-tenant + per-agent daily inference-cost caps.

Inference is the one place in ACP where each request directly maps to
external $$ (Groq / OpenAI / Anthropic) — a runaway agent can burn a
tenant's monthly budget in minutes. Sprint 1.1 + 3.2 give us per-tenant
RPS and request quotas; this module adds the dollar dimension:

* Token estimate → USD cost via a configurable price table
  ($/1000 input tokens, $/1000 output tokens).
* Two counters per call:
    - acp:inference_cost:tenant:{tenant_id}:{YYYY-MM-DD}
    - acp:inference_cost:agent:{agent_id}:{YYYY-MM-DD}
* On 80% crossing of either cap: one-shot warning via SETNX flag +
  Redis Stream event on `acp:billing_alerts`.
* On 100%: block the request with `limit_type="inference_cost"`. The
  middleware emits an audit row with `action="inference_cost_cap_exceeded"`.

The block side-effect is what makes the SLI bite — without it the
cap is just a dashboard number a customer can't trust.

Design notes:

* USD is stored as cents (int) in Redis to avoid INCRBYFLOAT drift —
  a long-running tenant could see µ-dollar rounding errors after
  millions of calls otherwise.
* The price table is intentionally configurable per call so a future
  multi-model gateway can pass per-request prices. Default is the
  conservative `_DEFAULT_PRICE_TABLE` below.
* Caller passes the estimated cost; this module is agnostic to which
  model produced the estimate. Callers SHOULD pass a high-watermark
  estimate (output_tokens=max_tokens) so the cap isn't bypassed by
  a "generate forever" prompt that the estimator thought was short.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

# Default tier — overridable per call. Conservative numbers; the
# real cost is paid downstream so this is just the cap accountant.
# These are deliberately rounded up: better to over-charge the limiter
# than to under-charge and miss a runaway.
_DEFAULT_PRICE_TABLE: dict[str, float] = {
    # $ per 1000 input tokens
    "groq_input_per_1k":     0.50,
    "groq_output_per_1k":    0.50,
}


@dataclass
class CostDecision:
    """Outcome of a cost-cap check.

    `allowed=False` blocks the request. `scope` ∈ {tenant, agent} when
    blocked — it's whichever cap was crossed first. On warning crossings
    (80%) `allowed` stays True and `warned=True`; the audit / alert
    side-effects fire from inside `check`.
    """
    allowed:        bool
    estimated_usd:  float = 0.0
    tenant_usd_used: float = 0.0
    agent_usd_used:  float = 0.0
    tenant_cap_usd: float = 0.0
    agent_cap_usd:  float = 0.0
    scope:          str | None = None
    warned:         bool = False
    reset_at:       str | None = None


class InferenceCostLimiter:
    """Two-axis daily USD cap (tenant AND agent).

    Returns a CostDecision; never mutates audit rows (the middleware
    owns that write so the audit_logs entry sits inside the request's
    transaction context).

    Storage:
      acp:inference_cost:tenant:{tenant_id}:{YYYY-MM-DD}  → cents (int)
      acp:inference_cost:agent:{agent_id}:{YYYY-MM-DD}    → cents (int)
      acp:inference_cost_warn:tenant:{tenant_id}:{YYYY-MM-DD}  SETNX flag
      acp:inference_cost_warn:agent:{agent_id}:{YYYY-MM-DD}    SETNX flag
    """

    BILLING_ALERTS_STREAM = "acp:billing_alerts"
    BILLING_ALERTS_MAXLEN = 10_000

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    # ── Token-cost estimator ──────────────────────────────────────────
    @staticmethod
    def estimate_cost_usd(
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        price_table: dict[str, float] | None = None,
    ) -> float:
        """Multiplier-based dollar estimate. Pure function — unit-tested
        against fixed tables so a price-table refactor doesn't silently
        change every customer's bill."""
        table = price_table or _DEFAULT_PRICE_TABLE
        return (
            (input_tokens / 1000.0) * float(table.get("groq_input_per_1k", 0.0))
            + (output_tokens / 1000.0) * float(table.get("groq_output_per_1k", 0.0))
        )

    # ── Public API ────────────────────────────────────────────────────
    async def check(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
        estimated_usd: float,
        tenant_cap_usd: float,
        agent_cap_usd: float,
    ) -> CostDecision:
        """Atomic two-axis cost check.

        Behavior:
          1. INCRBY each scope's cents counter by `int(estimated_usd*100)`.
          2. If the resulting cents > cap_cents → return blocked.
             Note: we increment FIRST, then decide — that makes the
             counter the source of truth for "how much did this tenant
             actually try to spend", which is what auditors want.
          3. On the threshold (≥80%) → fire one-shot warning side-effect.

        Caps of `0` or `None` are treated as "no cap on this axis".
        """
        now = datetime.now(tz=UTC)
        delta_cents = max(0, int(round(estimated_usd * 100.0)))

        tenant_used_cents = await self._incr_scope("tenant", tenant_id, now, delta_cents)
        agent_used_cents = 0
        if agent_id:
            agent_used_cents = await self._incr_scope("agent", agent_id, now, delta_cents)

        tenant_cap_cents = int(round((tenant_cap_usd or 0.0) * 100.0))
        agent_cap_cents  = int(round((agent_cap_usd or 0.0) * 100.0))

        # Block decision — tenant cap takes precedence over agent cap
        # so the audit row's `scope` is the most actionable signal.
        scope: str | None = None
        if tenant_cap_cents > 0 and tenant_used_cents > tenant_cap_cents:
            scope = "tenant"
        elif agent_cap_cents > 0 and agent_used_cents > agent_cap_cents:
            scope = "agent"

        decision = CostDecision(
            allowed=(scope is None),
            estimated_usd=round(estimated_usd, 4),
            tenant_usd_used=tenant_used_cents / 100.0,
            agent_usd_used=agent_used_cents / 100.0,
            tenant_cap_usd=tenant_cap_usd or 0.0,
            agent_cap_usd=agent_cap_usd or 0.0,
            scope=scope,
            reset_at=_next_utc_midnight(now).isoformat(),
        )

        # Warning crossing (80%) — fired once per scope/key/day. We do
        # this even on the BLOCKED path because the block happens on
        # the first call that crosses 100% — the customer needs the
        # 80% warning to be useful so they can take action before the
        # 100% guillotine drops on the next call.
        if tenant_cap_cents > 0:
            await self._maybe_warn(
                "tenant", tenant_id, used=tenant_used_cents, cap=tenant_cap_cents,
                now=now, decision=decision,
            )
        if agent_id and agent_cap_cents > 0:
            await self._maybe_warn(
                "agent", agent_id, used=agent_used_cents, cap=agent_cap_cents,
                now=now, decision=decision,
            )

        # Observability counter — fires on every call regardless of
        # block/allow, so dashboards can see "we billed this tenant
        # $X today".
        try:
            from sdk.utils import INFERENCE_COST_USD_TOTAL
            INFERENCE_COST_USD_TOTAL.labels(scope="tenant", key=tenant_id).inc(estimated_usd)
            if agent_id:
                INFERENCE_COST_USD_TOTAL.labels(scope="agent", key=agent_id).inc(estimated_usd)
        except Exception:
            pass

        # Blocked-counter — separate from the dollar counter so the
        # Alertmanager rule can fire on rate(blocked) directly.
        if not decision.allowed:
            try:
                from sdk.utils import INFERENCE_COST_BLOCKED_TOTAL
                key = tenant_id if scope == "tenant" else (agent_id or "<unknown>")
                INFERENCE_COST_BLOCKED_TOTAL.labels(scope=scope, key=key).inc()
            except Exception:
                pass

        return decision

    async def usage_snapshot(
        self,
        *,
        tenant_id: str,
        agent_id: str | None,
    ) -> dict[str, Any]:
        """Read-only counters — never INCRs. Used by `/tenant/quota` to
        surface inference-cost usage alongside the existing request quota."""
        now = datetime.now(tz=UTC)
        out: dict[str, Any] = {
            "tenant_usd_used":  await self._read_cents("tenant", tenant_id, now) / 100.0,
            "tenant_resets_at": _next_utc_midnight(now).isoformat(),
        }
        if agent_id:
            out["agent_usd_used"]  = await self._read_cents("agent", agent_id, now) / 100.0
            out["agent_resets_at"] = _next_utc_midnight(now).isoformat()
        return out

    # ── Internals ─────────────────────────────────────────────────────
    async def _incr_scope(
        self, scope: str, key: str, now: datetime, delta_cents: int,
    ) -> int:
        rkey = self._counter_key(scope, key, now)
        try:
            used = int(await self._redis.incrby(rkey, delta_cents))
            if used == delta_cents:
                # First INCR — set the daily TTL so the key clears on
                # rollover. 36h covers DST + clock skew.
                await self._redis.expire(rkey, 36 * 3600)
            return used
        except Exception:
            # Fail-open on the counter: under a Redis outage, the
            # gateway's existing rate-limit + the live billing pipeline
            # are the safety net. Don't block customer traffic on a
            # transient observability failure.
            return 0

    async def _read_cents(self, scope: str, key: str, now: datetime) -> int:
        rkey = self._counter_key(scope, key, now)
        try:
            raw = await self._redis.get(rkey)
            return int(raw or 0)
        except Exception:
            return 0

    async def _maybe_warn(
        self, scope: str, key: str, *,
        used: int, cap: int, now: datetime, decision: CostDecision,
    ) -> None:
        if cap <= 0:
            return
        if used < int(cap * 0.80):
            return
        warn_key = self._warn_key(scope, key, now)
        try:
            first = await self._redis.setnx(warn_key, "1")
            if not first:
                return
            await self._redis.expire(warn_key, 36 * 3600)
            await self._redis.xadd(
                self.BILLING_ALERTS_STREAM,
                {"data": json.dumps({
                    "kind":           "inference_cost_warning",
                    "scope":          scope,
                    "key":            key,
                    "used_usd":       used / 100.0,
                    "cap_usd":        cap / 100.0,
                    "percent":        round(used / cap * 100.0, 2),
                    "resets_at":      decision.reset_at,
                    "ts":             int(now.timestamp()),
                })},
                maxlen=self.BILLING_ALERTS_MAXLEN, approximate=True,
            )
            try:
                from sdk.utils import INFERENCE_COST_WARNING_TOTAL
                INFERENCE_COST_WARNING_TOTAL.labels(scope=scope, key=key).inc()
            except Exception:
                pass
            decision.warned = True
        except Exception:
            # Warnings are best-effort. Never break a request because
            # we couldn't tell ops about a near-cap.
            pass

    @staticmethod
    def _counter_key(scope: str, key: str, now: datetime) -> str:
        return f"acp:inference_cost:{scope}:{key}:{now.strftime('%Y-%m-%d')}"

    @staticmethod
    def _warn_key(scope: str, key: str, now: datetime) -> str:
        return f"acp:inference_cost_warn:{scope}:{key}:{now.strftime('%Y-%m-%d')}"


def _next_utc_midnight(now: datetime) -> datetime:
    nxt = (now + timedelta(days=1)).date()
    return datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC)
