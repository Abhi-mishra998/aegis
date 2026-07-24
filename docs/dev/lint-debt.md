# Lint debt ledger

Referenced by `.github/workflows/test.yml`. Living record of ruff /
mypy findings intentionally suppressed at the CI gate. Items graduate
into fixes over sprints (see `AUDIT_2026_07_21.md::S13`).

## Current CI ruleset

`ruff check . --select F,E9,W6 --ignore F841 --output-format=github`

- **F** (pyflakes) — undefined names, unused imports, invalid string
  formats, exceptions never raised. Always block.
- **E9** — syntax errors, IndentationError.
- **W6** — deprecated stdlib calls that will break in a future Python.
- **`--ignore F841`** — unused local variables. Cosmetic; mostly in
  tests where the binding is kept for readability. Bookmarked here for
  audit S13's auto-fix pass.

## Not enforced by CI (yet — audit S13)

Full `ruff check .` reports ~400 findings on the 2026-07-21 baseline.
Categories, by count from the last audit run:
- **I001** unsorted-imports: ~209 (auto-fixable).
- **UP017** `datetime.utcnow()` deprecations: ~25.
- **UP037** quoted annotations: ~24.
- **ANN201** missing return type on public function: ~14.
- **SIM105** suppressible-exception (candidates for
  `contextlib.suppress`): ~13.
- **C901** cyclomatic complexity: ~6 (see audit P2-1).
- **F841** unused-variable: ~6.

Plan (audit S13): run `ruff check --fix .` for the ~286 auto-fixable
findings, hand-fix `datetime.utcnow` + return types + F841, then
re-tighten the CI ruleset to include I / UP / ANN / SIM.

## `# noqa` without reason (audit S20)

**Current count**: 234 `# noqa: <CODE>` markers in `services/` + `sdk/`
(non-test) that have no trailing reason string.

**Standard**: every suppression should carry a one-line reason so a
future reader knows *why* the check was silenced.

```python
# ✗ Bad
foo(bar)  # noqa: BLE001

# ✓ Good
foo(bar)  # noqa: BLE001 — trust boundary, error surfaced via metric
```

**Plan**: bundle with the S13 ruff auto-fix pass. Regex to catch:
`# noqa($|: [A-Z][A-Z0-9]*([, ]+[A-Z][A-Z0-9]*)*$)`.

## Escape hatch

Downgrading the CI ruleset (removing rule classes, adding blanket
`# noqa`, or `continue-on-error`) is FORBIDDEN. If a finding needs to
be suppressed, it gets a `# noqa: XXX — reason` marker and the reason
lives here.
