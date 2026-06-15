"""
ARCH-6 2026-06-15 — Corpus regression runner.

Runs the 1000-scenario corpus through the LOCAL canonical + evaluate_full
pipeline (no network). The same canonical the gateway uses on every
/execute call.

Two modes:

  pytest tests/corpus/test_corpus.py
      → asserts pass-rate >= AEGIS_CORPUS_THRESHOLD (default 0.85).
        CI gate: if any commit drops pass-rate, the build fails.

  pytest tests/corpus/test_corpus.py --baseline
      → prints the current pass-rate + per-category breakdown.
        Use this to refresh the threshold after a deliberate change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from services.policy.canonical import normalize
from services.policy.local_action_semantics import evaluate_full

_THRESHOLD = float(os.environ.get("AEGIS_CORPUS_THRESHOLD", "0.95"))
_CORPUS_PATH = Path(__file__).parent / "corpus.json"


def _load_corpus() -> list[dict]:
    if not _CORPUS_PATH.exists():
        pytest.skip("corpus.json missing — run `python tests/corpus/generator.py`")
    return json.loads(_CORPUS_PATH.read_text())


def _evaluate_one(scenario: dict) -> dict:
    """Evaluate one scenario and return the result dict."""
    canonical = normalize(scenario["tool"], scenario["arguments"])
    full = evaluate_full({"canonical": canonical, **scenario["arguments"]})
    return {
        "id":               scenario["id"],
        "category":         scenario["category"],
        "expected_tier":    scenario["expected_tier"],
        "got_tier":         full["tier"],
        "findings":         full["findings"],
        "policy_id":        full["policy_id"],
        "expected_finding": scenario.get("expected_finding_substring") or "",
        "policy_prefix":    scenario.get("policy_id_prefix") or "",
    }


def _is_pass(r: dict) -> bool:
    if r["got_tier"] != r["expected_tier"]:
        return False
    # When the scenario specifies a finding substring, require it.
    ef = r["expected_finding"]
    if ef:
        if not any(ef in f for f in r["findings"]):
            return False
    pp = r["policy_prefix"]
    if pp and r["policy_id"] and not r["policy_id"].startswith(pp):
        return False
    return True


def test_corpus_pass_rate():
    corpus = _load_corpus()
    results = [_evaluate_one(s) for s in corpus]
    passes = sum(1 for r in results if _is_pass(r))
    rate = passes / len(results)

    by_cat: dict[str, tuple[int, int]] = {}
    fail_examples: list[dict] = []
    for r in results:
        ok = _is_pass(r)
        p, t = by_cat.get(r["category"], (0, 0))
        by_cat[r["category"]] = (p + (1 if ok else 0), t + 1)
        if not ok and len(fail_examples) < 12:
            fail_examples.append(r)

    print(f"\n=== Corpus pass-rate: {rate:.2%} ({passes}/{len(results)}) ===")
    for cat, (p, t) in sorted(by_cat.items()):
        print(f"  {cat:<20} {p}/{t}  ({p/t:.0%})")
    if fail_examples:
        print(f"\n  Up to 12 failing examples:")
        for r in fail_examples:
            print(f"    {r['id']:<12} expect={r['expected_tier']:<8} got={r['got_tier']:<8} pid={r['policy_id']:<22} findings={r['findings'][:3]}")

    assert rate >= _THRESHOLD, (
        f"Corpus pass-rate {rate:.2%} below threshold {_THRESHOLD:.0%}. "
        f"Run `python tests/corpus/generator.py` to refresh corpus or "
        f"export AEGIS_CORPUS_THRESHOLD=<new> if the drop is intentional."
    )


@pytest.mark.parametrize("category", [c[0] for c in [
    ("Healthcare", None), ("Finance", None), ("DevOps", None),
    ("Legal", None), ("HR", None), ("Supply Chain", None),
    ("Prompt Injection", None), ("Data Exfil", None),
    ("Identity Abuse", None), ("Insider Threat", None),
]])
def test_per_category_floor(category: str):
    """Every category must pass at least 70% — catches one-category regressions."""
    corpus = _load_corpus()
    cat_corpus = [s for s in corpus if s["category"] == category]
    results = [_evaluate_one(s) for s in cat_corpus]
    passes = sum(1 for r in results if _is_pass(r))
    rate = passes / len(results) if results else 0
    assert rate >= 0.70, (
        f"Category '{category}' pass-rate {rate:.0%} below 70% floor "
        f"({passes}/{len(results)})"
    )
