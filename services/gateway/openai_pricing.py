"""OpenAI model pricing — USD per 1M tokens (June 2026).

Source: openai.com/pricing. Same disclaimer as anthropic_pricing —
1% slippage vs the OpenAI invoice is acceptable for our per-employee
spend indicator; reconciliation against the real invoice is monthly.

Unknown model names fall back to gpt-4o-mini's rates so we don't
under-bill the customer but also don't show a 100x over-estimate.
"""
from __future__ import annotations

# (input_per_1m_usd, output_per_1m_usd)
_PRICING_USD_PER_1M_TOKENS: dict[str, tuple[float, float]] = {
    # Current GPT-4o family (Spring 2025 prices, still good through 2026)
    "gpt-4o":                     (2.50, 10.00),
    "gpt-4o-2024-11-20":          (2.50, 10.00),
    "gpt-4o-2024-08-06":          (2.50, 10.00),
    "gpt-4o-mini":                (0.15, 0.60),
    "gpt-4o-mini-2024-07-18":     (0.15, 0.60),

    # o-series reasoning models
    "o1":                         (15.00, 60.00),
    "o1-2024-12-17":              (15.00, 60.00),
    "o1-preview":                 (15.00, 60.00),
    "o1-mini":                    (3.00, 12.00),
    "o1-mini-2024-09-12":         (3.00, 12.00),
    "o3-mini":                    (1.10, 4.40),
    "o3-mini-2025-01-31":         (1.10, 4.40),

    # GPT-4 turbo + legacy
    "gpt-4-turbo":                (10.00, 30.00),
    "gpt-4-turbo-2024-04-09":     (10.00, 30.00),
    "gpt-4":                      (30.00, 60.00),
    "gpt-4-32k":                  (60.00, 120.00),

    # GPT-3.5 (legacy but still requested)
    "gpt-3.5-turbo":              (0.50, 1.50),
    "gpt-3.5-turbo-0125":         (0.50, 1.50),
}

_FALLBACK_INPUT_PER_1M:  float = 0.15
_FALLBACK_OUTPUT_PER_1M: float = 0.60


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost of one OpenAI /chat/completions call.

    Best-effort — unknown model → gpt-4o-mini rates. Returns a
    non-negative float; never raises.
    """
    rates = _PRICING_USD_PER_1M_TOKENS.get(
        (model or "").lower(),
        (_FALLBACK_INPUT_PER_1M, _FALLBACK_OUTPUT_PER_1M),
    )
    return (
        max(0, int(input_tokens or 0)) * rates[0]
        + max(0, int(output_tokens or 0)) * rates[1]
    ) / 1_000_000.0
