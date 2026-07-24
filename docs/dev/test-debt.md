# Test debt ledger

Referenced by `.github/workflows/test.yml`. Living record of tests that
fail on a fresh checkout without a live Docker stack. Items get owners
and land as fixes in the audit's follow-up sprint queue
(`AUDIT_2026_07_21.md`).

## Categories

### 1. MockRedis fixture is incomplete
Tests: `test_api_key_auth`, `test_mesh_auth`, `test_n16_n20_ssrf_hardening`,
several `test_phase*_*` tests.
Cause: `MockRedis` in the test harness lacks `.lpush`, `.pipeline`,
`.publish`, `.hgetall` methods used by the middleware chain.
Owner / fix: extend MockRedis surface or gate these tests behind a live
Redis fixture. Audit follow-up: not in the sprint queue — file when a
maintainer picks it up.

### 2. Live-stack-only tests running by default
Tests: `test_phase*` cluster (~105 failures on fresh checkout).
Cause: These tests hit routes that call Redis / Postgres. Should be
marked `@pytest.mark.integration` so they are deselected under the
default `-m "not integration"` filter.
Owner / fix: add the marker; move from `tests/` to `tests/integration/`.

### 3. Mesh JWT key material not present in unit-test env
Tests: `test_mesh_auth::test_*`, several `test_demo_workspace_blocked_paths`
scenarios.
Cause: The tests exercise the `X-Mesh-Token` verifier which requires
`ACP_MESH_PRIVATE_KEY_PEM` / `ACP_MESH_TRUSTED_KEYS`. CI does not
inject those.
Owner / fix: either seed the CI env with an ephemeral ES256 keypair or
mark these `@pytest.mark.integration`.

### 4. UI presence tests
Tests: `test_roadmap_deliverables.py::test_*` (login SSO buttons,
pricing tiers, dashboard components).
Cause: Assert existence of specific JSX / HTML text in the UI bundle.
The Playwright job at `.github/workflows/test.yml::e2e-playwright` is
the canonical home for these. Currently skipped (arm64 SHA-pin
mismatch — audit S12).
Owner / fix: move to Playwright + close audit S12.

### 5. Test debt closed in the 2026-07-21 audit session
- `test_verify_role.py::test_malformed_header_returns_401` — updated to
  match P3-1/N17 hardened error detail.
- `test_verify_role.py::test_validator_failure_returns_401_with_detail` — same.
- `test_wizard.py::test_wizard_request_defaults_risk_to_medium` → renamed
  `test_wizard_request_defaults_risk_to_none` (Sprint 13 moved derivation
  to wizard execution time).
- `test_sql_normalization.py::test_normalize_for_detection_microbench` —
  perf threshold relaxed from 500μs → 2ms to tolerate full-suite CPU
  contention; still catches O(N) regressions.

## Escape hatch

`continue-on-error` on the unit-tests job is FORBIDDEN. If a legitimate
red flag turns into an emergency (e.g., a live-stack test starts running
in the unit suite by accident), open a PR that either (a) adds the
`integration` marker, or (b) fixes the test, or (c) deletes it. Do not
re-add the flag.
