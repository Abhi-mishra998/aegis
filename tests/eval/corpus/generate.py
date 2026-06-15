"""Sprint 5 — Generate corpus.jsonl from base_attacks.py × mutators.py.

Run from the repo root:

    python3 -m tests.eval.corpus.generate

That produces ``tests/eval/corpus/corpus.jsonl`` — one JSON object per
line — and prints a summary table (cases per OWASP category, mutations
per attack, attack/benign split).

The file is checked into git so the seed loader and the runner don't
need to re-generate on every invocation. Regenerate it whenever you add
or modify base attacks; the diff makes the intended change reviewable.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from tests.eval.corpus.base_attacks import ALL_BASE
from tests.eval.corpus.mutators import MUTATORS, apply_mutation


OUT_PATH = Path(__file__).parent / "corpus.jsonl"


# Per-category mutation augmentations applied on top of each base's
# declared mutations. LLM01 (prompt injection) benefits most from
# obfuscation testing — homoglyphs and multilingual cues are the variants
# detectors most often miss. LLM07 (SQL/cmd injection) benefits from
# base64-wrapped payloads since attackers regularly smuggle them inside
# json blobs.
_CATEGORY_EXTRA_MUTATIONS: dict[str, tuple[str, ...]] = {
    "LLM01": ("base64", "homoglyph", "multilingual"),
    "LLM07": ("base64",),
}


def _build_case(base: dict, mutation: str) -> dict:
    payload_str = apply_mutation(base["payload"], mutation)
    return {
        "case_kind":        base["case_kind"],
        "owasp_category":   base["owasp_category"],
        "base_id":          base["base_id"],
        "mutation":         mutation,
        "tool":             base["tool"],
        "payload":          payload_str,
        "expected_outcome": base["expected_outcome"],
        "expected_findings": base["expected_findings"],
        "target_rule":      base["target_rule"],
        "notes":            base.get("notes", ""),
    }


def _mutations_for(base: dict) -> tuple[str, ...]:
    base_mut = tuple(base["mutations"])
    extra = _CATEGORY_EXTRA_MUTATIONS.get(base["owasp_category"], ())
    seen: set[str] = set()
    combined: list[str] = []
    for m in base_mut + extra:
        if m not in seen:
            combined.append(m)
            seen.add(m)
    return tuple(combined)


def build_cases() -> list[dict]:
    cases: list[dict] = []
    for base in ALL_BASE:
        for mutation in _mutations_for(base):
            if mutation not in MUTATORS:
                raise ValueError(
                    f"base {base['base_id']} requests unknown mutation: {mutation}"
                )
            cases.append(_build_case(base, mutation))
    return cases


def write_corpus(cases: list[dict], path: Path = OUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")


def print_summary(cases: list[dict]) -> None:
    by_owasp = Counter(c["owasp_category"] for c in cases)
    by_kind = Counter(c["case_kind"] for c in cases)
    by_mutation = Counter(c["mutation"] for c in cases)
    by_tool = Counter(c["tool"] for c in cases)

    print(f"Wrote {len(cases)} cases to {OUT_PATH}\n")

    print("Cases per OWASP category:")
    for cat in sorted(by_owasp):
        print(f"  {cat:<12} {by_owasp[cat]:>5}")
    print()

    print("Cases per kind:")
    for kind in sorted(by_kind):
        print(f"  {kind:<12} {by_kind[kind]:>5}")
    print()

    print("Cases per mutation:")
    for mut in sorted(by_mutation):
        print(f"  {mut:<14} {by_mutation[mut]:>5}")
    print()

    print("Cases per tool:")
    for tool in sorted(by_tool):
        print(f"  {tool:<22} {by_tool[tool]:>4}")
    print()


def main() -> int:
    cases = build_cases()
    write_corpus(cases)
    print_summary(cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
