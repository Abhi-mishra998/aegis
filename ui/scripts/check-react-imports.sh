#!/usr/bin/env bash
# Asserts every .jsx/.tsx file that uses a React hook also imports it.
#
# Catches the exact bug class that took Team.jsx down on 2026-06-24: a batch
# refactor added `useRef(false)` to a file whose import line didn't include
# `useRef`. Vite compiles JSX without verifying named imports exist in
# module scope, so the build went green and the bundle shipped a runtime
# `ReferenceError: useRef is not defined` that bricked the page.
#
# Heuristic: for each hook, find files that reference the bare identifier
# but neither destructure-import it from 'react' nor use the React.<hook>
# namespaced form. Skip false-positive matches from comments and from
# react-router-dom hooks like `useSearchParams` / `useNavigate` (those are
# legitimately imported from a different module).
set -euo pipefail
cd "$(dirname "$0")/.."

REACT_HOOKS=(useRef useState useEffect useCallback useMemo useContext useLayoutEffect useReducer useImperativeHandle useDebugValue useDeferredValue useTransition useSyncExternalStore useId)

violations=0
for hook in "${REACT_HOOKS[@]}"; do
  while IFS= read -r f; do
    # Skip if the file destructure-imports the hook from 'react' (with
    # either single or double quotes around 'react').
    if grep -qE "^import[^;]*\{[^}]*\b$hook\b[^}]*\}[^;]*from ['\"]react['\"]" "$f"; then
      continue
    fi
    # Skip if the file uses the React.<hook> namespaced form (covered by
    # a plain `import React from 'react'`).
    if grep -qE "\bReact\.$hook\b" "$f"; then
      continue
    fi
    # Skip lines where the hook only appears inside a comment or a string —
    # cheap heuristic: require the hook followed by `(` (call site) on at
    # least one non-comment line.
    if ! grep -E "^[^/]*\b$hook\s*\(" "$f" | grep -vE '^\s*(//|\*)' | grep -q .; then
      continue
    fi
    echo "ERROR: $f uses $hook but does not import it from 'react'" >&2
    violations=$((violations + 1))
  done < <(grep -RIl "\b$hook\b" src/ 2>/dev/null | grep -E '\.(jsx?|tsx?)$')
done

if [ "$violations" -gt 0 ]; then
  echo "FAILED: $violations React-hook import violation(s) found" >&2
  exit 1
fi
echo "✓ all React-hook callers import their hooks"
