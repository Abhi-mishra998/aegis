"""Sprint 8 — Rego drift gate.

`services/policy/policies/action_semantics_deny.rego` carries Rego
mirrors of the catalog's pattern lists. This test fails the build when
the on-disk Rego no longer matches what `rego_emitter` would emit from
the current catalog — i.e. when a contributor added a pattern to one
side and forgot the other.

To fix locally:

    python -m services.policy.rego_emitter --write
"""
from __future__ import annotations

from services.policy import rego_emitter


def test_rego_matches_generated_output():
    ok, msg = rego_emitter.check()
    assert ok, (
        "Rego is out of sync with services/policy/pattern_catalog.py.\n"
        "Run `python -m services.policy.rego_emitter --write` to fix.\n"
        f"First diff lines:\n{msg}"
    )


def test_emitter_render_set_quotes_each_value():
    """Tiny smoke test — the emitter's internal helper produces a
    Rego set literal of the expected shape."""
    s = rego_emitter._render_set("foo", ["a.io", "b.io"])
    assert s == '_foo := { "a.io", "b.io" }'


def test_emitter_render_blocks_contains_both_generated_sets():
    blocks = rego_emitter.render_generated_blocks()
    assert "exfil_hosts" in blocks
    assert "offshore_tokens" in blocks
    assert "transfer.sh" in blocks["exfil_hosts"]
    assert "cayman" in blocks["offshore_tokens"]
