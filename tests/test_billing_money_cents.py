"""Q36 regression: billing money-saved counter must use integer cents
(HINCRBY), not floats (HINCRBYFLOAT). Redis HINCRBYFLOAT accumulates
IEEE-754 drift over many small increments — after N thousand billing
events the customer-facing "money saved" number is provably wrong on
the money path.

Fix stores canonical `money_saved_cents` (int) + read path divides by
100 to return USD. Legacy `money_saved` (float) is still summed on
read for backward compat with pre-fix rows.
"""
from __future__ import annotations

import inspect


def test_write_path_uses_hincrby_not_hincrbyfloat():
    from services.usage.billing_routes.value_engine import BillingValueEngine
    src = inspect.getsource(BillingValueEngine.record_protection_event)
    # New integer-cents writes present.
    assert 'hincrby(daily_key, "money_saved_cents"' in src
    assert 'hincrby(monthly_key, "money_saved_cents"' in src
    assert 'hincrby(daily_key, "cost_prevented_cents"' in src
    # Old float writes are gone.
    assert 'hincrbyfloat(daily_key, "money_saved"' not in src
    assert 'hincrbyfloat(monthly_key, "money_saved"' not in src
    assert 'hincrbyfloat(daily_key, "cost_prevented"' not in src


def test_read_path_sums_cents_and_legacy_usd():
    """Read must combine both fields so historical data isn't dropped
    when the version boundary rolls out."""
    from services.usage.billing_routes.value_engine import BillingValueEngine
    src = inspect.getsource(BillingValueEngine.get_tenant_billing_summary)
    # The helper that sums both fields exists.
    assert "def _usd(" in src
    # It reads both the new cents field AND the legacy USD field.
    assert 'd.get(key_cents' in src
    assert 'd.get(key_legacy_usd' in src


def test_integer_cents_exact_over_many_increments():
    """Sanity: integer-cent accumulation is bit-exact regardless of
    how many increments run — that's the whole point of the switch.
    Python's sum() on floats may be exact for some inputs but not
    others (rounding is input-dependent); the fix removes the class
    entirely by using ints."""
    total_cents = sum(int(round(0.01 * 100)) for _ in range(10_000))
    assert total_cents == 10_000  # $100 in cents, exact + stable across pythons
