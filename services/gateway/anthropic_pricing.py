"""Anthropic model pricing — USD per 1M tokens (June 2026).

Source: anthropic.com/pricing. Numbers below are approximations rounded
to the nearest cent — they're the basis of the Sprint 17 per-employee
spend rollup, so a 1% slippage vs the real Anthropic invoice is
acceptable for a usage indicator. The reconciliation against the actual
invoice happens monthly in the Stripe webhook flow (Sprint 9).

If a model isn't in the table we fall back to the Haiku rate
(conservative for the customer's bill, generous for our usage
indicator).
"""
from __future__ import annotations

# (input_per_1m_usd, output_per_1m_usd)
_PRICING_USD_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # Claude Opus 4.7 (current SOTA reasoning)
    "claude-opus-4-7":              (15.00, 75.00),
    "claude-opus-4-7-20250101":     (15.00, 75.00),
    # Claude Sonnet 4.6
    "claude-sonnet-4-6":            (3.00, 15.00),
    "claude-sonnet-4-6-20251001":   (3.00, 15.00),
    # Claude Haiku 4.5 (cheapest current)
    "claude-haiku-4-5":             (0.80, 4.00),
    "claude-haiku-4-5-20251001":    (0.80, 4.00),
    # Legacy snapshots — keep working for customers who haven't upgraded
    "claude-opus-4-5":              (15.00, 75.00),
    "claude-sonnet-4-5":            (3.00, 15.00),
    "claude-haiku-4-0":             (1.00, 5.00),
    "claude-3-5-sonnet-20241022":   (3.00, 15.00),
    "claude-3-5-haiku-20241022":    (0.80, 4.00),
}

# Fallback if we see an unknown model name — use Haiku rates so we don't
# under-bill but also don't terrify the customer with a 75x over-estimate.
_FALLBACK_INPUT_PER_1M:  float = 0.80
_FALLBACK_OUTPUT_PER_1M: float = 4.00


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost of one Anthropic Messages call.

    Best-effort — if the model is unknown we use the Haiku rate. Returns
    a positive float; never raises.
    """
    rates = _PRICING_USD_PER_1M_TOKENS.get(
        model or "",
        (_FALLBACK_INPUT_PER_1M, _FALLBACK_OUTPUT_PER_1M),
    )
    input_cost  = (input_tokens or 0)  / 1_000_000 * rates[0]
    output_cost = (output_tokens or 0) / 1_000_000 * rates[1]
    return round(input_cost + output_cost, 6)
