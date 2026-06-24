#!/usr/bin/env bash
# verify_all_units.sh — coordinator helper for the 2026-06-24 docs/UI batch.
# Runs the per-unit grep verifications that each of the 10 worker branches
# promised. Prints PASS/FAIL per unit; exits non-zero if any unit fails.
# No arguments; safe to re-run; touches nothing.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

if [[ "${1-}" == "--help" || "${1-}" == "-h" ]]; then
  echo "usage: $0   # verify doc/UI invariants from the 10-unit batch"
  exit 0
fi

fail=0
DOCS="setup-agies.md ui-setup.md README.md integrations/README.md docs/README.md"

check() { # name, hits
  if [[ -z "$2" ]]; then echo "PASS  $1"
  else echo "FAIL  $1"; echo "$2" | sed 's/^/      /'; fail=1; fi
}

# Unit 09 — Documentation freshness pass.
# Migration callouts that explain the ha.aegisagent.in retirement are allowed.
check "09a docs no longer reference ha.aegisagent.in (outside migration callouts)" \
  "$(grep -nE 'ha\.aegisagent\.in' $DOCS 2>/dev/null \
     | grep -viE 'retired|legacy|consolidation' || true)"

# http://localhost is only allowed inside a fenced block that carries the
# <!-- intentional local dev --> sentinel within 15 lines above the match.
unmarked=""
for f in $DOCS; do
  while IFS=: read -r line content; do
    [[ -z "$line" ]] && continue
    start=$(( line > 15 ? line - 15 : 1 ))
    sed -n "${start},${line}p" "$f" | grep -qE 'intentional local dev' \
      || unmarked+="$f:$line:$content"$'\n'
  done < <(grep -nE 'http://localhost' "$f" 2>/dev/null || true)
done
check "09b docs have no unmarked http://localhost" "$unmarked"

check "09c verify_all_units.sh exists and is executable" \
  "$(test -x scripts/ops/verify_all_units.sh || echo 'missing or not executable')"

check "09d README.md contains the 2026-06-24 What's new section" \
  "$(grep -q "## What's new (2026-06-24)" README.md || echo "missing What's new header")"

exit "$fail"
