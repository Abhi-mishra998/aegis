"""ATF v3.2 §4.4 — Aegis Profile issuance quota.

Tenants get a contractual limit N on concurrent Aegis Profiles. Attempts
past N do NOT throw — they succeed with a `C2` ledgered event marking
the mint as quota-adjacent, so an auditor sees the profile-mint burst
as an anomaly (§4.4: "quota-bounded, attributable, and evident").

Pure counter helper against a caller-supplied storage callable —
Redis in production, dict in tests.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

MintOutcome = Literal["ALLOWED", "QUOTA_EXCEEDED"]


@dataclass
class MintDecision:
    outcome: MintOutcome
    current_count: int
    quota: int
    should_ledger_c2: bool  # True iff we should raise a C2 alert entry


def evaluate_mint(
    *,
    active_count: int,
    quota: int,
) -> MintDecision:
    if active_count >= quota:
        return MintDecision(
            outcome="QUOTA_EXCEEDED",
            current_count=active_count,
            quota=quota,
            should_ledger_c2=True,
        )
    # Approaching the ceiling (last 5%) — surface an alert so the customer
    # can bump the contract before it starts blocking.
    approaching = active_count >= max(quota - 5, int(quota * 0.95))
    return MintDecision(
        outcome="ALLOWED",
        current_count=active_count,
        quota=quota,
        should_ledger_c2=approaching,
    )


def enforce_mint(
    *,
    active_count_loader: Callable[[], int],
    active_count_incrementer: Callable[[], int],
    quota: int,
) -> MintDecision:
    """Real caller flow: read → decide → increment on ALLOWED."""
    current = active_count_loader()
    d = evaluate_mint(active_count=current, quota=quota)
    if d.outcome == "ALLOWED":
        new_count = active_count_incrementer()
        return MintDecision(
            outcome=d.outcome,
            current_count=new_count,
            quota=quota,
            should_ledger_c2=d.should_ledger_c2,
        )
    return d


if __name__ == "__main__":
    # Fresh tenant well under quota
    d = evaluate_mint(active_count=10, quota=100)
    assert d.outcome == "ALLOWED"
    assert not d.should_ledger_c2

    # Approaching ceiling — allowed but flagged for C2 alert
    d = evaluate_mint(active_count=96, quota=100)
    assert d.outcome == "ALLOWED"
    assert d.should_ledger_c2

    # Over ceiling — blocked and C2 alert
    d = evaluate_mint(active_count=100, quota=100)
    assert d.outcome == "QUOTA_EXCEEDED"
    assert d.should_ledger_c2

    # enforce_mint increments only when ALLOWED
    store = {"n": 10}
    d = enforce_mint(
        active_count_loader=lambda: store["n"],
        active_count_incrementer=lambda: (store.__setitem__("n", store["n"] + 1) or store["n"]),
        quota=100,
    )
    assert d.outcome == "ALLOWED"
    assert store["n"] == 11

    store["n"] = 100
    d = enforce_mint(
        active_count_loader=lambda: store["n"],
        active_count_incrementer=lambda: (_ for _ in ()).throw(RuntimeError("must not increment")),
        quota=100,
    )
    assert d.outcome == "QUOTA_EXCEEDED"
    assert store["n"] == 100

    print("tenant_quota OK")
