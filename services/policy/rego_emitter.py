"""Sprint 8 — generate Rego pattern lists from the shared catalog.

Rego mirrors of `EXFIL_HOSTS` and `OFFSHORE_TOKENS` keep drifting from
the Python source because they're hand-maintained. This module emits
the Rego for those lists from `pattern_catalog.py` and rewrites the
sentinel-delimited blocks in `policies/action_semantics_deny.rego`.

CLI:

  python -m services.policy.rego_emitter --check       # exit 1 on drift
  python -m services.policy.rego_emitter --write       # rewrite in place

A pytest in `tests/policy/test_rego_drift.py` calls `check()` so CI
fails when a contributor adds a new pattern to the catalog but not to
the Rego (or vice versa).

The rule logic (`_known_exfil_url if ...`) stays hand-written —
Sprint 8 ships pattern-list convergence, not a full Python→Rego
transpiler.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Iterable


# Anchor: project root resolved from the file path so the script works
# whether invoked from the repo root, an IDE test runner, or CI.
_THIS_FILE = pathlib.Path(__file__).resolve()
_REGO_PATH = _THIS_FILE.parent / "policies" / "action_semantics_deny.rego"


# Each generated block is wrapped in matched begin/end sentinels so the
# rewriter can replace just the body. Anything outside the sentinels is
# preserved verbatim — that's where the hand-written rule logic lives.
_BEGIN = "# --- BEGIN GENERATED:{name} ---"
_END   = "# --- END GENERATED:{name} ---"


def _render_set(name: str, values: Iterable[str]) -> str:
    """Render a Rego `set` literal."""
    quoted = [f'"{v}"' for v in values]
    return f"_{name} := {{ {', '.join(quoted)} }}"


def render_generated_blocks() -> dict[str, str]:
    """Return {block_name: rendered Rego body}.

    The names match the sentinels in the Rego file — keep them in
    sync if you add a new block here. The body is JUST the rendered
    Rego (no sentinels); the rewriter adds those.
    """
    # Imported lazily so this module can be imported in test contexts
    # where the policy package may be re-stubbed.
    from services.policy.pattern_catalog import EXFIL_HOSTS, OFFSHORE_TOKENS
    return {
        "exfil_hosts":      _render_set("exfil_hosts", EXFIL_HOSTS),
        "offshore_tokens":  _render_set("offshore_tokens", OFFSHORE_TOKENS),
    }


def _find_block(content: str, name: str) -> tuple[int, int] | None:
    """Return (start_after_begin, end_before_end) inclusive line indices,
    or None when the sentinels aren't present yet."""
    begin = _BEGIN.format(name=name)
    end   = _END.format(name=name)
    lines = content.splitlines()
    b_idx = None
    e_idx = None
    for i, line in enumerate(lines):
        if line.strip() == begin:
            b_idx = i
        elif line.strip() == end:
            e_idx = i
            break
    if b_idx is None or e_idx is None or e_idx < b_idx:
        return None
    return b_idx, e_idx


def _splice(content: str, name: str, body: str) -> str:
    """Replace the body between the named sentinels with `body`.

    Raises ValueError when the sentinels aren't present — the Rego
    file needs them added manually once; after that the rewriter
    maintains them.
    """
    span = _find_block(content, name)
    if span is None:
        raise ValueError(
            f"sentinels for {name!r} not found in {_REGO_PATH.name}; "
            "add `# --- BEGIN GENERATED:{name} ---` / "
            "`# --- END GENERATED:{name} ---` lines once."
        )
    lines = content.splitlines()
    b_idx, e_idx = span
    new_lines = lines[: b_idx + 1] + [body] + lines[e_idx:]
    return "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")


def render_rego() -> str:
    """Apply every generated block to the current Rego file contents.

    Read → splice each block → return new contents. Pure function.
    """
    content = _REGO_PATH.read_text()
    for name, body in render_generated_blocks().items():
        content = _splice(content, name, body)
    return content


def check() -> tuple[bool, str]:
    """Return (ok, diff_summary). `ok=True` means the Rego on disk is
    in sync with the catalog. CLI returns exit 1 on drift."""
    expected = render_rego()
    actual = _REGO_PATH.read_text()
    if expected == actual:
        return True, "ok"
    # Cheap diff summary — full unified diff is noisy and pulls in
    # difflib for no real benefit at CI exit-status level.
    diff_lines: list[str] = []
    for i, (e, a) in enumerate(zip(expected.splitlines(), actual.splitlines())):
        if e != a:
            diff_lines.append(f"line {i + 1}: expected={e!r} actual={a!r}")
            if len(diff_lines) >= 5:
                break
    if not diff_lines:
        diff_lines.append("line count differs")
    return False, "\n".join(diff_lines)


def write() -> bool:
    """Rewrite the Rego file in place. Returns True if anything changed."""
    expected = render_rego()
    actual = _REGO_PATH.read_text()
    if expected == actual:
        return False
    _REGO_PATH.write_text(expected)
    return True


def _main() -> int:
    ap = argparse.ArgumentParser(description="Rego generator for Sprint 8.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="Exit 1 if Rego is out of sync with the catalog.")
    group.add_argument("--write", action="store_true",
                       help="Rewrite the Rego file in place.")
    args = ap.parse_args()
    if args.check:
        ok, msg = check()
        if ok:
            print(f"rego in sync ({_REGO_PATH.name})")
            return 0
        print(f"rego DRIFT detected ({_REGO_PATH.name}):\n{msg}", file=sys.stderr)
        return 1
    if args.write:
        changed = write()
        print(f"{'rewrote' if changed else 'no changes'} {_REGO_PATH.name}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
