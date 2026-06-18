"""B1 regression guard — wire-transfer escalation floor at $100k.

Audit finding B1 (closed in commit 943d83c, 2026-06-18):
  Before the fix the gateway pattern detector
  (services/gateway/escalation_patterns.py:39-52) fired at $100k+, but the
  policy enforcement at services/policy/local_action_semantics.py:101 and
  services/policy/policies/action_semantics_deny.rego:501 only fired at
  $200k+. A $150k external wire matched the pattern (CFO routing card
  emitted) but escaped Rego/local enforcement — production-routing bug.

  Fix aligned all three layers at $100k. This test asserts the local
  fast-path layer (evaluate_full) — the layer SDK / API-key callers hit.

The unit boundary tested here is the dict returned by evaluate_full().
CFO routing itself is downstream in services/policy/packs.py:197 and
services/gateway/escalation_patterns.py:43 (those are exercised by the
E2E rows E18 / E19 in SPRINT.md §12).
"""
from __future__ import annotations

import pytest

from services.policy.local_action_semantics import (
    TIER_ALLOW,
    TIER_DENY,
    TIER_ESCALATE,
    evaluate_full,
)


def _wire(amount_usd: int, recipient_kind: str) -> dict:
    """Minimal canonical-style arg bag for a wire/payment tool call.

    Mirrors what the gateway middleware extractor populates from a
    `tool.payment_transfer` / `tool.wire` call before handing the bag to
    the local fast-path. `recipient_kind` is the discriminator: external,
    offshore, unknown all match the allowlist; internal does not.
    """
    return {"amount_usd": amount_usd, "recipient_kind": recipient_kind}


# ---------------------------------------------------------------------------
# The five spec rows from the kickoff prompt (Phase 2).
# ---------------------------------------------------------------------------

def test_99k_external_allows() -> None:
    """Just under the $100k floor — no escalate, no deny."""
    result = evaluate_full(_wire(99_000, "external"))
    assert result["tier"] == TIER_ALLOW, (
        f"$99k external must allow; got {result}"
    )
    assert "money_transfer_external" not in result["findings"]


def test_100k_external_escalates() -> None:
    """The floor itself — the boundary the B1 fix aligned."""
    result = evaluate_full(_wire(100_000, "external"))
    assert result["tier"] == TIER_ESCALATE, (
        f"$100k external must escalate; got {result}"
    )
    assert result["policy_id"] == "FIN-WIRE-002"
    assert "money_transfer_external" in result["findings"]


def test_150k_external_escalates_b1_gap_closure() -> None:
    """THE B1 gap. Pre-fix this matched the pattern detector but escaped
    Rego/local enforcement at $200k. Post-fix it escalates through every
    layer.
    """
    result = evaluate_full(_wire(150_000, "external"))
    assert result["tier"] == TIER_ESCALATE, (
        f"$150k external (B1 gap closure) must escalate; got {result}"
    )
    assert result["policy_id"] == "FIN-WIRE-002"
    assert "money_transfer_external" in result["findings"]


def test_1m_external_escalates() -> None:
    """Well above the floor, well below the $10M hard cap."""
    result = evaluate_full(_wire(1_000_000, "external"))
    assert result["tier"] == TIER_ESCALATE, (
        f"$1M external must escalate; got {result}"
    )
    assert result["policy_id"] == "FIN-WIRE-002"


def test_99k_internal_allows() -> None:
    """recipient_kind=internal stays out of the external/offshore/unknown
    allowlist regardless of amount.
    """
    result = evaluate_full(_wire(99_000, "internal"))
    assert result["tier"] == TIER_ALLOW, (
        f"$99k internal must allow; got {result}"
    )


# ---------------------------------------------------------------------------
# Bonus regression guards — protect adjacent invariants from drift.
# ---------------------------------------------------------------------------

def test_10m_external_hard_deny_intact() -> None:
    """Lowering the escalate floor must not weaken the $10M absolute cap.
    FIN-WIRE-001 fires before FIN-WIRE-002.
    """
    result = evaluate_full(_wire(10_000_000, "external"))
    assert result["tier"] == TIER_DENY, (
        f"$10M external must hard-deny; got {result}"
    )
    assert result["policy_id"] == "FIN-WIRE-001"
    assert "money_transfer_above_hard_cap" in result["findings"]


def test_150k_internal_allows() -> None:
    """recipient_kind discriminator at the B1 gap amount: $150k is enough
    to trigger IF the destination is external — but an internal corporate
    sweep at the same amount is fine. Proves the amount alone is not the
    trigger.
    """
    result = evaluate_full(_wire(150_000, "internal"))
    assert result["tier"] == TIER_ALLOW, (
        f"$150k internal must allow (no external destination); got {result}"
    )


@pytest.mark.parametrize("recipient_kind", ["external", "offshore", "unknown"])
def test_100k_external_allowlist_recipients_all_escalate(recipient_kind: str) -> None:
    """All three recipient_kind values in the escalate allowlist must
    fire at the $100k floor. Catches drift where one of them gets
    dropped from the tuple in evaluate_full().
    """
    result = evaluate_full(_wire(100_000, recipient_kind))
    assert result["tier"] == TIER_ESCALATE, (
        f"$100k {recipient_kind} must escalate; got {result}"
    )
    assert result["policy_id"] == "FIN-WIRE-002"
