# Aegis Audit Follow-Through — 2026-07-21

Ledger over `AUDIT_2026_07_21.md`. Shipped vs. still owed.
Critical path S1 → S11 closed earlier this session. This second pass
knocks off the hygiene batch sprint-by-sprint.

## Shipped

### Critical path (S1 → S11)

| Sprint | Finding | Result |
|---|---|---|
| S1 | P0-1 (part) | ✓ Root-fixed `MetaData` collision. Collection: 2810 → 3126 tests, 0 errors. |
| S2 | P0-1 (rest) | ✓ CI hard-fail restored; 4 `--ignore` flags gone. |
| S3 | P0-5 | ✓ `billing_status` split off `audit_logs`; new migration + writer UPSERT + all readers JOIN sibling. |
| S5 | P0-4 + P1-8 | ✓ `swallow_log` helper + `EXCEPTION_SWALLOWED_TOTAL` metric; 2 auth-boundary DELETEs; ~68 additional fail-open sites migrated across audit/api/behavior/policy/security/gateway/registry/forensics. BUSYGROUP `xgroup_create` swallows left intentional (semantic-check). |
| S6 | P1-5 | ✓ `OPA_FAIL_MODE=open` in prod refuses to boot. |
| S7 | P1-6 | ✓ Atomic Redis-Lua reserve + reconcile; applied at both racing sites. |
| S8 | P1-7 | ✓ X-Org-ID server-side, always. Strict SaaS invariant runs on all methods. |
| S9 | P1-9 | ✓ Autonomy SSRF validator deleted; canonical `sdk.common.outbound_url_allowlist` at all 4 dispatch sites. |
| S10 | P1-10 | ✓ PUT-time `validate_outbound_url` on remediation + threat-intel URL sinks. |
| S11 | P2-7 | ✓ `X-Internal-Secret` removed from CORS `allow_headers`. |
| Test-debt (round 1) | — | ✓ 4 test-debt tests updated. Full suite: 2585 → 2589 pass. |

### Hygiene batch (this pass)

| Sprint | Finding | Result |
|---|---|---|
| P1-17 | P1-17 | ✓ Destructive-action rego rule in `agent_policy.rego`; ARE `_policy_gate` threads `metadata.approval_id`; `_DESTRUCTIVE` set now includes `REVOKE_KEY`. Rego denies if no approval id. |
| S11a | P1-11 | ✓ Registry `_invalidate_agent_caches` uses `swallow_log`; the 4 caller `contextlib.suppress` wraps deleted (inner handler now complete). |
| S11c | P2-9 | ✓ Redundant inline `_rate_limit_401` decorator deleted from `gateway/main.py`; `SecurityMiddleware` internal 401-burst counter is the single gate. Misleading comment removed. |
| S11d | P2-10 | ✓ Wizard `_whitelist_default_tools` returns `(added, failed_tools)`; `WizardCreatedResponse.tools_whitelist_failed` surfaces transient partial failures to the UI. |
| S11e | P2-11 | ✓ `list_registered_tools` hard-cap `_AGENT_SCAN_CAP=5000` + `_PERM_SCAN_CAP=10_000`; truncation logs a warning. |
| S11f | P2-12 | ✓ Registry quarantine rolls back the Redis flag + returns 500 when the DB persist fails (was silently 200). |
| S11g | P1-13 | ✓ `Depends(verify_internal_secret)` added to `/internal/throttle` in api service. |
| S11i | P1-15 | ✓ Behavior `/check` returns `{success: False, error: "invalid uuid: …"}` on parse fail; tenant-less `history_key` fallback deleted from `service.py`. |
| S11j | P1-16 | ✓ Insight cross-tenant SCAN fallback deleted; synthetic path already handles fresh-tenant empty case. |
| S11l | P2-14 | ✓ ARE `_store_pending_approval` mints via `secrets.token_urlsafe(16)`; `request_id` moved from key to payload. |
| S11n | P2-16 | ✓ Behavior `cross_agent_risk` floor `0.3` + `cross_agent_correlation_unavailable` flag on intelligence-engine failure. |
| S16 | P1-3 | ✓ `docs/dev/test-debt.md` + `docs/dev/lint-debt.md` created. |
| S18b | P2-8 | ✓ Clerk `verify_aud=True` gated by `CLERK_AUDIENCE` setting (default `"aegis"`). |

## Pending — ops task

| Sprint | Finding | Owner |
|---|---|---|
| S4 | P0-3 | You / ops — apply `alembic upgrade head` per updated `MIGRATION_PENDING.md`. |
| S12 | P0-2 | infra — arm64 SHA multi-arch swap + re-enable e2e. |
| S17 | P1-4 | ops — `alembic current` across 9 services + reconcile. |

## Owed — hygiene sprints (next passes)

**S batch (5 items):**
- [x] **S11b** — `require_admin_role` dep on 7 registry write endpoints (`services/registry/router.py`).
- [x] **S11h** — Repository filters on `(tenant_id, agent_id)`; composite unique via `s11h_composite_tenant_agent_unique_2026_07_21.py`.
- [x] **S11k** — `_VISITED_CAP = 5000` in BFS (`services/identity_graph/repository.py`).
- [x] **S11m** — `_assert_agent_in_tenant` helper called at 5 forensics endpoints.
- [ ] **S14** — Land `batch/08-sdk-shared-base` (`_AegisIntegrationBase._call_execute`; 4 SDK integrations subclass). *Ops-deferred (needs PyPI publish).*

**S batch (structural):**
- [x] **S15** — `tier_final`/`findings_final`/`policy_id_final` on `main` rule; `check_policy` returns 4-tuple.
- [x] **S18** — Signer fingerprint returns full 64-char SHA-256; `_fingerprint_matches` accepts both truncated (32) and full (64) forms; backfill migration `s18_full_signer_fingerprint_2026_07_21.py`.
- [x] **S20** — Bare `# noqa` audit: 0 offenders across `services/` + `sdk/` (all 271 noqas carry rule codes). `git clean -fdX` / venv rebuild / branch triage deferred (destructive, ops).

**M–L batch (last):**
- [x] **S5-continue** — Done (this pass); see S5 row above.
- [x] **S13** — Ruff clean: 235 → 0 findings. 225 auto-fixed, 4 real bugs surgically fixed (SIM105, B023, ANN001/202), E701/E702 accepted for compact score-band lookups.
- [x] **S19** — Split done. `messages.py` (1977 → ~830 lines: `/v1/messages` proxy + Slack approve/reject) + new `messages_dashboard.py` (~1180 lines: team/employees, team/overview, employees/{email}/profile, dashboard/overview, approvals/{id}/status, replay/{id}). Router mounted alongside in `gateway/main.py`.
- [x] **P2-17** — Async task hygiene. Grep-classified 47 bare `create_task` sites: 40 lifespan-daemon or request-scoped `.gather` (correct as-is). 6 real fire-and-forget writers wrapped in `safe_bg`: `autonomy/incident_watcher.py:351/354/403` (Jira/SNOW/playbook), `security/incidents/recorder.py:271` (remediation), `gateway/routers/demo.py:1356/1360` (seed + demo traffic).
- [x] **P3-2** — `CLERK_PLACEHOLDER_HASH` extracted to `services/identity/__init__.py`; `clerk_provision.py` + `webhooks_clerk.py` both import.
- [x] **P3-3** — `services/policy/rego_emitter.py` prints verified inside `__main__` CLI mode — correct pattern, no fix needed.

## 2026-07-22 enterprise wiring push (W1-W3 shipped, W4-W8 queued)

**W1 — Multi-IdP acceptance layer (shipped, real crypto tests).**
- New `services/gateway/idp_verifiers.py` — dispatcher for SPIFFE / Entra / Okta with JWKS in-process LRU + Redis cache, uniform `ACPAuthError("Unauthorized")` on every failure (no oracle).
- `services/gateway/auth.py::LocalTokenValidator.validate` tries the 3 external adapters BEFORE Clerk / legacy; each adapter is OFF unless its config is set.
- 6 new settings in `sdk/common/config.py`: `SPIFFE_TRUST_DOMAIN`, `SPIFFE_TRUST_BUNDLE_JSON`, `SPIFFE_AUDIENCE`, `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE`, `ENTRA_JWKS_CACHE_SECONDS`, `OKTA_ISSUER`, `OKTA_AUDIENCE`, `OKTA_JWKS_CACHE_SECONDS`.
- 11 new tests in `services/gateway/tests/test_idp_verifiers.py` — real RSA keypair generation, forged-token rejection, wrong-trust-domain rejection, expired-token rejection, uniform-error assertion. All pass.

**W2 — Tenant issuance quota enforcement (shipped).**
- `services/registry/router.py::create_agent` gates every mint via `sdk/common/tenant_quota.evaluate_mint`. Atomic Redis counter `acp:tenant:profile_count:{tenant_id}`.
- QUOTA_EXCEEDED → HTTP 429 + C2 `tenant_profile_quota_exceeded` audit event.
- 95% headroom → C2 `tenant_profile_quota_approaching` audit event on every mint until under threshold.
- SaaS invariant failure → counter rolled back via DECR so failed mints don't leak slots.
- Quota infra blip fails OPEN (not closed) — DoSing every mint on a Redis blip would be worse than one over-shoot; 95% alert catches drift.
- New setting `TENANT_PROFILE_QUOTA_DEFAULT=1000`.

**W3 — ATF v3 export bundle endpoint (shipped).**
- `GET /logs/export-atf-v3` on the audit service. Returns the §7.3 bundle shape via `atf_export_bundle.build_bundle`, entries projected through `atf_entry.to_atf_entry`. Bounded to ≤ 10k entries (large exports use existing NDJSON `/export`).
- Real summary counts (action_class, verdicts, escalations, contradictions), period start/end from actual rows, current `POLICY_MANIFEST_HASH` embedded, `bundle_version="3.0"` self-declared per §7.4.

**W4 — Consistency sampling for C3 (shipped).**
- New `services/policy/c3_gate.py` — `should_sample(action_class, tenant_id)` opt-in gate + async `evaluate(planner)`; planner errors propagate (silent swallow would defeat the point).
- Wired into `services/gateway/routers/messages.py` — cheap short-circuit when disabled; when C3 + tenant enabled, calls upstream 3× and blocks on INCONSISTENT / NEEDS_HUMAN.
- 9 tests in `services/policy/tests/test_c3_gate.py`.
- New env: `ACP_C3_SAMPLING_TENANTS`.

**W5 — SCIM agent reconciler (shipped).**
- `sdk/common/scim_client.py` — async httpx wrapper, ACTIVE / SUSPENDED / NOT_FOUND / transient; 10 real tests via `MockTransport`.
- `services/identity/scim_reconciler.py` — `run_once_async(agents)` prefetches concurrently, feeds `scim_agent.reconcile`; 3 tests including SCIM outage → no mass quarantine.
- `services/identity/scim_router.py` — `POST /scim/reconcile` mounted on identity service.
- New settings: `SCIM_BASE_URL`, `SCIM_BEARER_TOKEN`, `SCIM_RECONCILE_TIMEOUT_SECONDS`.

**W6 — Escalation channel selector (shipped).**
- `fire_teams()` + `fire_webhook()` in `services/autonomy/webhook_executor.py` with same SSRF guard + no-redirect rules as existing helpers.
- Teams renders Adaptive Card with FactSet from context; webhook posts arbitrary JSON body.
- 7 real tests in `services/autonomy/tests/test_channel_dispatch.py`.

**W7 — Collusion detector subscriber (shipped).**
- `_collusion_loop()` in `services/identity_graph/worker.py`: every 5 min per tenant, 24h edge rollup → `label_propagation_communities` (min_edge_weight=3) → alerts on ≥3-member communities with drift ≥0.6 via `collusion_suspicion` DriftSignals.
- Wired into `services/identity_graph/main.py` lifespan alongside trust_scorer + drift_detector.
- New env: `COLLUSION_INTERVAL_S`, `COLLUSION_WINDOW_HOURS`, `COLLUSION_MIN_EDGE_WEIGHT`, `COLLUSION_MIN_CLUSTER`, `COLLUSION_DRIFT_THRESHOLD`.

**W8 — behavior_opt_in enforcement (shipped).**
- `gate_score_consumption(tenant_id, "display")` wired at `services/behavior/service.py:307` — the exact site that invokes learned `intelligence_engine.report_anomaly`. Tenants NOT opted in get ML-derived cross_agent_risk skipped + `learned_fingerprinting_disabled_for_tenant` flag emitted.
- Deterministic signals (sequence, velocity, cost) fire regardless — per ATF §9.2.
- 6 tests in `sdk/common/tests/test_behavior_opt_in_wire.py` including "gate_input ALWAYS refused" invariant.

**All 8 W-sprints shipped. Final suite: 1855 pass / 0 fail / ruff clean.**

## 2026-07-22 quality-review pass — TOCTOU fix

**Q1 — Tenant quota atomic (shipped, closed race).**
**Q2 — 3 real bugs caught in self-review + fixed:**
- Typo `"C1": 1 * 0` → `"C1": 0` in `services/audit/router.py:1646` (cosmetic — 1*0 == 0, but future-brittle).
- **Dead-code trap**: `services/identity/scim_reconciler.py::run_once` was `async` but called `asyncio.run()` inside → `RuntimeError` if ever invoked from a running loop. Deleted; `run_once_async` (the wired one) works.
- **Missing role gate**: `POST /lifecycle/transition` — any authenticated tenant user could transition state, but transitions are C3 events per §14.5. Added `Depends(verify_role(Role.OWNER))` matching the workspace shadow-mode-exit pattern.

**Q28 — Exfiltration detector's `credential_in_message_body` silently missed case-sensitive secret patterns (AKIA-, JWT-eyJ-, ghp_-) when only lowercased `raw_norm` reached it.**

`services/security/objectives/exfiltration.py` did `raw_orig = c.get("raw_norm_original") or c.get("raw_norm") or ""` then ran every `_SECRET_PATTERNS` regex against that single string. If a caller passed only `raw_norm` (test paths, stale cached canonical results from pre-U13 rollout), the AWS `AKIA[0-9A-Z]{16}\b`, JWT `\beyJ...`, and GitHub `ghp_[A-Za-z0-9]{30,}` case-sensitive patterns silently missed on lowercased input — a security-relevant under-detection on the primary credential-exfil surface.

Fix: build a `haystacks` tuple of every non-empty raw source (`raw_norm_original`, `raw_norm`) and run each pattern against every haystack when both are present. Strict improvement in true-positive rate; no false-positive change (each pattern's own case sensitivity still governs whether it matches — lowercase input can't match `AKIA[0-9A-Z]{16}` and doesn't try).

**8 new tests** in `tests/test_credential_detector_case_sensitivity.py`: AKIA + JWT original-case detected (sanity), pattern-in-only-original still detected (both haystacks searched), pattern-in-only-lowercase still detected, single-source fallbacks work, empty-input no false-positive, canary that documents the old fallback was really broken (regex behavior verified).

**Suite: 2081 pass / same 2 unrelated env-only failures / ruff clean.**

**Q27 — `generate_dpdp_bundle` had the same unbounded-row-load pattern as Q26 (EU AI Act).**

`services/audit/compliance.py::generate_dpdp_bundle::§8(5)+§8(7)` did `tool_rows: list[AuditLog] = list((await db.execute(tool_q)).scalars().all())` on the tenant's `execute_tool` audit rows in the period with no LIMIT — same OOM shape as EU AI Act, same regulator-facing endpoint (India DPDP compliance bundle). Fix: swap in the same `_tally_execute_tool_calls_sql` aggregator introduced for Q26; total_signed_records + by_tool + by_decision all come from SQL now.

**1 new test**: whitebox regression that DPDP's source references the SQL aggregator + the old unbounded pattern is gone (mirrors the EU AI Act regression from Q26). No end-to-end test for DPDP existed to break; the sweep grepped every other `list(...scalars().all())` in `compliance.py` and confirmed the remaining sites carry `.limit(N)` in the query.

**Suite: 2073 pass / same 2 unrelated env-only failures / ruff clean.**

**Q26 — `generate_eu_ai_act_bundle` loaded every matching audit row into Python — OOM on a busy tenant's year-long compliance window.**

`services/audit/compliance.py::generate_eu_ai_act_bundle` did `list(tool_result.scalars().all())` on `tool_q = base_q.where(action=="execute_tool")` with no LIMIT. On the §12.1 reference workload (1M actions/day) a 1-year regulator request = 365M rows, an OOM waiting to happen the first time a customer's auditor asked for a real compliance bundle.

Fix: extracted `_tally_execute_tool_calls_sql(db, tenant_id, start, end) -> (total, by_tool, by_decision, first_id, last_id)` that aggregates entirely in SQL — 5 indexed queries (COUNT, GROUP BY tool, GROUP BY lower(decision), first/last ID via LIMIT 1). Python memory is O(unique tools × unique decisions) — a small constant — regardless of row volume in the period.

**3 new tests** in `tests/test_compliance_bundle_aggregation.py`: whitebox check that the caller uses the SQL path (regression prevents reverting), mocked-db assertion that the aggregator packs the return tuple correctly + issues exactly 5 queries (any extra means a row-fetching query snuck in), empty-period edge case (total=0, empty dicts, None ids). Updated `tests/test_compliance.py::test_eu_ai_act_bundle_structure` to match the new db.execute call sequence (5 aggregation calls + 3 remaining).

**Suite: 2072 pass / same 2 unrelated env-only failures / ruff clean.**

**Q25 — ATF §14.5 ROTATE cross-signing of the retiring key's final batch.**

Spec text (§14.5 ROTATE): *"old key's final batch cross-signed by new key so chain verification survives rotation."* Fingerprint-dispatched historical keys (B3) closed the LOOKUP side, but the transition batch itself had no signature under the new key — creating a "gap batch" verifiers couldn't cover without waiting for the first fresh post-rotation batch.

Shipped:
- Migration `s14_5_rotate_cross_signature_2026_07_24` — three nullable columns on `transparency_historical_keys`: `transition_root_hash`, `transition_new_key_signature`, `transition_new_key_fingerprint`.
- `scripts/maintenance/rotate_transparency_key.py` — new `_fetch_last_root_for_key` + `_cross_sign_payload` helpers; before writing the historical row, script fetches the retiring key's latest TransparencyRoot, signs its canonical `signed_root_payload` with the NEW key, and INSERTs the historical row with all three transition fields populated in one atomic op. First rotation on a fresh deployment (no roots yet) leaves the transition fields NULL — that's the well-defined "no cross-signature possible" state.
- `services/audit/signer.py::verify_rotation_cross_signature(historical_row, new_key_pem, old_signed_root_payload) -> bool` — offline verifier. Returns False (never raises) on missing transition fields (legacy pre-2026-07-24 rows), fingerprint mismatch, root_hash mismatch, or signature-invalid. Post-2026-07-24 rotations MUST evaluate True; a False on a fresh rotation is an ops-page-worthy anomaly.
- `services/audit/models.py::TransparencyHistoricalKey` — three new nullable columns declared on the model.

**6 new tests** in `tests/test_rotation_cross_signature.py`: happy-path verify, legacy no-fields → False, wrong-new-key rejected, mismatched root_hash rejected (belt+suspenders check), tampered signature rejected, partial transition fields rejected (no half-verified states).

**Q24 — ATF §14.5 DESTROY certificate.**

Spec text (§14.5 DESTROY): *"destruction produces a signed certificate referencing the final anchor — the customer can forever prove what existed and when it was destroyed."* The lifecycle transition endpoint already recorded the state change; the certificate ARTIFACT itself was missing.

Shipped:
- `sdk/common/destruction_certificate.py` — pure module. `build_destruction_certificate(...)` composes the cert dict + signs canonical body via a caller-supplied `sign` callable. `verify_destruction_certificate(cert, public_key_pem)` for offline verification. Refuses (`RetentionFloorNotMet`) if actual retention < required floor — the cert NEVER understates retention, which is its whole point. Retention floor default is 180d per §7.3.
- `POST /logs/destruction-certificate` on the audit service (`services/audit/router.py`) — fetches the tenant's most recent TransparencyRoot + first/last audit_logs timestamps, calls the pure builder, returns the signed cert. Refuses 409 on retention violation, 404 when no anchors/entries exist.
- Gateway proxy `POST /audit/logs/destruction-certificate` (`services/gateway/routers/audit.py`).
- Lifecycle hook (`services/gateway/routers/lifecycle.py`) — when a transition sets state = DESTROY, the endpoint automatically fetches the certificate from the audit service and returns it in the transition response body under `destruction_certificate` (or `destruction_certificate_error` if generation failed). The certificate can be re-issued via the direct endpoint for as long as audit rows remain on disk — failure to attach in the transition response does NOT roll the transition back.

**15 new tests** in `tests/test_destruction_certificate.py`: happy-path verify, retention floor honored (actual == floor → OK, actual < floor → refused, negative floor rejected, final-before-first rejected), tamper detection (retention days / tenant_id / final_anchor.root_hash mutations → False; swapped signature bytes → False; wrong public key → False), missing-field discrimination (signature / final_anchor / algorithm), canonicalization invariant (published `canonical_body_sha256` equals `sha256(canonical_json(body))`).

**Suite: 2069 pass / same 2 unrelated env-only failures / ruff clean.**

**Q23 — POST /auth/tenants had four numeric fields + a tier enum coercion that still 500'd on bad input despite the endpoint docstring promising 4xx.**

`services/identity/router.py::upsert_tenant` was hardened in the 2026-06-24 QA-VALIDATION-FIX sprint to catch every invalid-shape input and return 422. But when Sprint 3.2 quota fields (`requests_per_second`, `burst`, `daily_request_cap`, `monthly_request_cap`, `daily_inference_cost_cap_usd`) landed later, they were added as bare `int(body.get(...))` and `float(body.get(...))` — an operator sending `{"requests_per_second": "abc"}` would 500 the endpoint instead of getting a clean 400. Same silent regression for tier: `TenantTier(tier_val)` was not wrapped, so a typo'd tier (`"basik"`) would 500 at the SQL insert path.

Fixes:
- Extracted `_as_int(field, default)` helper that catches `ValueError`/`TypeError` and re-raises as 422 with a structured detail.
- Wired all four numeric fields through it.
- Added explicit try/except around `TenantTier(tier_val)`, mirroring the `DegradedModePolicy` guard already present.
- Wrapped `float(cost_cap_raw)` similarly.
- Used the pre-coerced `tier_enum` in both the update and insert branches instead of re-calling `TenantTier(tier_val)` twice.

**5 new canary tests** in `tests/test_tenant_upsert_validation.py` locking in the primitives the guard depends on: `TenantTier("mystery")` raises ValueError, `int("abc")` raises ValueError, `int(None)` raises TypeError, plus positive-case round-trip. If python semantics ever change or someone reverts the enum wrap, the underlying assumption fires visibly. Full endpoint-level test skipped because identity conftest needs Postgres and the fix is a uniform application of an existing tested pattern.

**Suite: 2048 pass / same 2 unrelated env-only failures / ruff clean.**

**Q22 — /auth/sso/config/test was an SSRF pivot: any authenticated tenant caller could point `issuer` at 169.254.169.254 (AWS IMDSv1) and the identity worker would fetch it with `follow_redirects=True` and no body cap.**

`services/identity/router.py::test_sso_config` built `test_url = f"{issuer}/.well-known/openid-configuration"` from the tenant-supplied issuer and did `httpx.AsyncClient(timeout=8.0, follow_redirects=True).get(test_url)` followed by `.json()`. Three separate exposures at one call site:
1. **SSRF** — no allowlist on the URL, so private CIDR / link-local (metadata endpoints) all reachable.
2. **Redirect chaining** — `follow_redirects=True` amplifies (hostile IdP redirects to internal target after the first hop).
3. **OOM** — `.json()` buffers the full body; a hostile IdP streaming an infinite response would OOM the identity worker.

Fix: added `validate_outbound_url(test_url, allowed_schemes=("http", "https"))` at the top of the fetch — blocks link_local / loopback / private_cidr / bad schemes with a clean `url_blocked: <reason>` status. Switched to `follow_redirects=False`. Rewrote the fetch as `client.stream("GET", ...)` with `aiter_bytes` + 1 MiB byte cap; over-cap returns `body_too_large` status instead of OOMing. JSON parse hardened: non-dict top-level body no longer fed to `.get("issuer", ...)`.

**6 new tests** in `tests/test_sso_test_endpoint_ssrf.py` locking in the SSRF guard's behavior against known attack shapes (AWS metadata IPv4/IPv6, loopback, private CIDR, file:// scheme, positive-case public IP). Tests hit the guard directly rather than spinning up the identity FastAPI app + Postgres, because the guard IS the security boundary.

**Suite: 2043 pass / same 2 unrelated env-only failures / ruff clean.**

**Q21 — SCIM client's `resp.json()` had no body-size cap; a broken or MITM'd directory could OOM the reconciler mid-batch.**

`sdk/common/scim_client.py::ScimClient.lookup_user` did `resp = await client.get(url)` then `body = resp.json()`. httpx buffers the FULL body before the caller sees the response. A customer's SCIM directory that regressed or was MITM'd could stream gigabytes and OOM the reconciliation loop — a periodic job that iterates EVERY registered agent, so one failure blocks all identity reconciliation.

Fix: switched to `client.stream("GET", url)` with `aiter_bytes` + running byte counter. Abort at `_SCIM_MAX_BYTES = 1 MiB` (env-tunable via `SCIM_MAX_RESPONSE_BYTES`) with a `ScimTransientError` — reconciler treats that as transient (keeps existing state) rather than mass-quarantining every agent on a directory hiccup. Also tightened downstream JSON handling: `_json.JSONDecodeError` becomes `scim_bad_json` transient, and a top-level non-object response becomes `scim_body_not_object` transient (defends the `body.get("active")` call against SCIM directories that mistakenly return a bare array).

**4 new tests** in `sdk/common/tests/test_scim_client.py::TestResponseBodyCap`: over-cap → transient, malformed JSON → transient, non-object body → transient, cap-size sanity bounds. All 17 pre-existing SCIM tests still pass — the streaming rewrite is behavior-preserving for the happy path.

**Suite: 2037 pass / same 2 unrelated env-only failures / ruff clean.**

**Q20 — Token revocation had a hardcoded 24h TTL; a revoked token would resurrect if `JWT_EXPIRY_MINUTES` was ever set past 1440.**

`services/identity/token_service.py::revoke` did `setex(revoke_key, 86400, "1")` — a fixed 24h expiry regardless of the token's own lifetime. Aegis default is 15min so this is safe at defaults, but any operator running long-lived service tokens (agents, CI, integration tokens with 48h/72h TTL) would have revocation silently expire before the token itself → **resurrection window** where a revoked token starts verifying again.

Fix (revoke): decode the token (already done for validity check, now capture the payload) and compute `revoke_ttl = max((exp - now) + 60, 60)`. Falls back to 86400 only if the token has no exp claim. Fix (revoke_all_for_agent): operates on hashes with no access to payload, so uses `redis.ttl(active_key)` — the active key was set at issuance to `expiry_seconds + 60`, so its remaining TTL is the token's remaining lifetime.

**4 new tests** in `tests/test_token_revoke_ttl.py`: 48h-token gets ~48h revoke TTL (regression on resurrection window), expired-token gets 60s floor (not zero/negative), revoke_all_for_agent picks up TTL from active_key, plus a canary that asserts `86400 < 48h*3600` so the whole premise stays evident.

**Suite: 2033 pass / same 2 unrelated env-only failures / ruff clean.**

**Q19 — Two enterprise admin endpoints (/admin/tenants/{id}/export + /redact) had a bit-rotted argv contract; both would fail at first customer use.**

`services/gateway/routers/tenant_admin.py` invoked the ops scripts with `--tenant-id`, but `scripts/ops/export_tenant.py` and `scripts/ops/redact_tenant_pii.py` both declare `--tenant` in their argparser. `--redact` was additionally missing `--reason` (required by the script for GDPR audit trail) and `--execute` (mutually-exclusive-group mode selector — without it, the script would drop into an interactive prompt or error out). Bit-rot from a rename of the argparse flags that only updated one side.

Fixes:
- `/admin/tenants/{id}/export` — switched to `--tenant`.
- `/admin/tenants/{id}/redact` — switched to `--tenant`, added `--reason` (read from request body — endpoint now validates `body["reason"]` alongside the existing confirm-token), added `--execute` so the script actually performs the redaction. Body-schema-error surfaces the expected shape in the 400 payload.

**4 new tests** in `tests/test_tenant_admin_argv_contract.py`: load each script's `_build_argparser()` and assert the exact argv the gateway builds parses cleanly. Two negative cases prove the old broken shapes (`--tenant-id`, missing `--reason`) STILL fail at argparse — locks in the fix without depending on the scripts to catch it themselves.

**Suite: 2028 pass / same 2 unrelated env-only failures / ruff clean.**

**Q18 — Sweep: five anchored regexes used `.match` instead of `.fullmatch`, letting trailing `\n` slip through.**

Root cause: `re.match(r"^...$", "foo\n")` returns truthy — python's `

 default matches BEFORE a trailing `\n`. Same class as Q15 but broader; grepped all `re.compile(r"^...$")` uses and audited each caller:

- `services/gateway/routers/auth.py::AuthRequest._check_email` — `_EMAIL_RE.match(v)` accepted `foo@bar.baz\n`. **SMTP header-injection surface** if the address is later used in outbound mail headers. Fixed.
- `services/policy/router.py::upload_policy` — `_NAME_RE.match(payload.name)` accepted `policy\n` → filename with newline written to `/tmp/acp_policies/{tenant}/policy\n.rego` + log-injection surface. Admin-role-gated so risk is lower but defense in depth. Fixed.
- `sdk/common/scim_client.py::_is_safe_scim_id` — accepted `id\n` → smuggled into URL construction. Fixed.
- `services/audit/public_transparency.py::_is_smoke_kid` — safe because it already does `.strip()` on the input before matching. NOT fixed (not a bug).
- `sdk/common/queue_age.py::_STREAM_ID_PATTERN` — group-extraction of leading digits from Redis stream ID; safe because `\d` doesn't consume `\n` and downstream only uses the captured group. NOT fixed (not a bug).

**7 new tests** in `tests/test_regex_fullmatch_hardening.py` locking in the fullmatch conversion for all three fixed validators + a canary that alerts if python's regex semantics ever change.

**Suite: 2024 pass / same 2 unrelated env-only failures / ruff clean.**

**Q17 — OIDC discovery + JWKS fetches had no body-size cap; hostile/MITM'd IdP could OOM the identity service.**

`services/identity/oidc.py::_get_discovery` and `_get_jwks` both called `resp.json()` after a plain `httpx.AsyncClient.get(...)` — httpx buffers the FULL response body into memory before returning. A network attacker (or a compromised IdP feed) could return an infinite response and OOM the identity worker. Real OIDC discovery docs + JWKS are a few KB; anything past 1 MiB is an attack.

Fix: new `_fetch_json_capped(url, method, data, headers, timeout)` helper streams the response with `client.stream(method, url, ...)` + `aiter_bytes`, aborting early via `ValueError` at `_IDP_MAX_BYTES = 1 MiB`. Wired at ALL FOUR IdP-response sites: discovery, JWKS, token exchange (`POST` form-encoded — higher-severity because it authenticates OUR clients), and userinfo. **5 new tests** in `tests/test_oidc_body_cap.py`: small doc round-trips, over-cap raises, 5xx propagates (cap doesn't hide upstream errors), cap-size sanity bounds, POST variant also caps.

**Suite: 2017 pass / same 2 unrelated env-only failures (email-validator, boto3 missing) / ruff clean.**

**Q16 — Gateway trust_proxy forwarder was vulnerable to cross-service path traversal via httpx URL normalization.**

Every `{full_path:path}` catch-all in `services/gateway/routers/proxies.py` forwards through `trust_proxy(base_url, path, request)` which built the URL by `url = f"{base_url.rstrip('/')}{path}"`. httpx normalizes `..` at URL build time (verified via `httpx.URL('http://x/a/b/../c').path == '/a/c'`), so a client hitting `/graph/../autonomy/admin` on the gateway would forward to `{GRAPH_URL}/autonomy/admin` — crossing the per-service scope boundary. Same class as the witness_proxy + SCIM guards, applied at the shared forwarder chokepoint rather than per-route.

Fix: `trust_proxy` now refuses any path containing `..`, CR, LF, or NUL BEFORE URL construction, returning `400 invalid path`. Regular FastAPI `{param}` routes (non-`:path`) already 404 on `..` at the routing layer, so the guard only fires on the 4 catch-alls (graph, flight, autonomy, and the witness_proxy heartbeat which has its own `_is_safe_witness_id`). **6 new tests**: `..` in various positions, CR/LF/NUL rejected, single `.` still passes guard.

**Q15 — witness_proxy path-safety regex allowed trailing newline + missed raw `..` sequences.**

Followup to the initial `_is_safe_witness_id` — 2 test failures caught:
- Python's `re.match` on `^...$` anchors matches BEFORE a trailing `\n` by default, so `witness\n` slipped through. Fixed by switching to `re.fullmatch`.
- The `..` traversal guard only checked `/..` variants (`"/.." in wid`, `startswith("..")`, etc.). A raw `witness..node` was accepted because it contained no `/`. Simplified to `if ".." in wid: return False` — catches every traversal shape at once.

**Q14 — SCIM reconciler had unbounded concurrent HTTP calls; DoS on customer's directory + our own connection pool.**

`run_once_async` did `tasks = {ref: asyncio.create_task(client.lookup_user(ref)) for ref in unique_refs}` — one task per unique SCIM ref. A tenant with 500 unique `human_responsible` values → 500 concurrent HTTP requests to the customer's SCIM directory. Two failure modes: (a) trips typical SCIM rate limits (Okta caps at ~150 rps for read-heavy tenants), (b) exhausts our httpx `AsyncClient` connection pool.

Fix: introduced `SCIM_RECONCILE_CONCURRENCY=32` (env-tunable) and a `_bounded_lookup` wrapper that acquires an `asyncio.Semaphore` before calling. Stays under customer rate limits AND still batches across the ref set (500 refs / 32 = ~16 batches ≈ 8s at 500ms latency).

**2 new tests** in `tests/test_scim_concurrency_bound.py`: instrumented stub client tracks peak in-flight count → asserts `peak ≤ cap` (with `cap=8`, 100 refs → peak stays ≤ 8) AND `peak > 1` (semaphore isn't accidentally serializing). Second test: all 100 refs still reconciled → no lookups dropped.

**Suite: 1996 pass / 0 fail / ruff clean.**

**Q13 — Real DoS bug in the witness memory fallback, found + fixed:**

Witness memory-fallback's `_observations` dict had NO cap on distinct `gate_decision_id` keys. Redis backend inherits its bound from TTL + `maxmemory-policy`; the fallback dict grew forever. Threat: witness in degraded memory mode (Redis down) + any mesh-authenticated caller streaming observations for many distinct gate ids → OOM. `record_observation` IS gated by mesh JWT (higher bar than untrusted internet) but defense in depth still applies to degraded modes.

Fix: `_MemoryFallback._observations` is now an `OrderedDict`; new `_MEMFB_MAX_GATE_IDS = 50000` (env-tunable) enforced on every insert of a NEW gate id. Existing gate ids get `move_to_end` for LRU-hot behavior — a burst of writes to one gate id doesn't cause it to be evicted just because it landed early. Eviction path emits `logger.critical("witness_memfb_gate_evicted", ...)` with counts so ops sees the evidence-loss signal.

**2 new tests**: 150 distinct gate ids into a cap of 100 → exactly 100 remain, oldest 50 evicted; touching an existing gate id promotes it (A,B,C written, A touched, D inserted → B evicted, A survives).

**Suite: 1994 pass / 0 fail / ruff clean.**

**Category B — 4 sprints closing the last shippable ATF gaps:**

- **B1 Serverless-UNOBSERVED marker** (§6.1 + Appendix D.1). Added `WITNESS_DEPLOYMENT_MODE=sidecar|serverless` env; `serverless` forces every verdict to `UNOBSERVED` with empty evidence — a misconfigured serverless deploy can't silently claim CORROBORATED. Unknown mode falls back to safe default `sidecar`. `/witness/health` surfaces `deployment_mode`. 8 tests: mode parsing (default sidecar, uppercase normalized, unknown → safe default), serverless verdict is UNOBSERVED even when store has observations + evidence list is empty, sidecar mode preserves prior behavior, health surfaces both modes.
- **B2 Provenance block enrichment** (§4.3). New `sdk/common/provenance_enrichment.py::enrich_from_env` reads `AEGIS_MODEL_REF`, `AEGIS_PROMPT_TEMPLATE_HASH`, `AEGIS_TOOL_MANIFEST_HASH`, `AEGIS_CONTAINER_IMAGE_DIGEST`, `AEGIS_SBOM_REF`. Missing OR empty-string → `None` (identical profile hash — no fabrication). Wired at `registry/router.py::create_agent`. 6 tests including profile-hash determinism (absent env vs empty env produce identical fingerprint) and populated-provenance-changes-hash sanity.
- **B3 Anchor rotation-survival test** (§7.4). Read the existing `scripts/maintenance/rotate_transparency_key.py` + `services/audit/signer.py`. **Design discovery** documented in the test file: Aegis uses fingerprint-dispatched verification via `transparency_historical_keys` registry, NOT literal cross-signing. That mechanism achieves the same property. Wrote real crypto test proving pre-rotation receipts still verify post-rotation via `verify_receipt_against_known_keys` fingerprint fallback; empty-registry case fails LOUD not silent. 4 tests.
- **B4 Witness trust-boundary doc** (§6.1). `docs/security/witness-trust-boundary.md`. States exactly what's trusted (host kernel, container runtime, signing-key storage, Redis), what's defended (compromised agent process only), the two bounded properties (heartbeat-loss visibility, anchor-bounded taint), what's NOT claimed (malicious host, kernel compromise, key-in-hand attacker, serverless evidence), and an operator checklist. Enterprise auditor-ready.

**Suite: 1992 pass / 0 fail / ruff clean.**

**Q12 — Two more real bugs fixed:**

- **`release_quota_slot` swallowed ALL exceptions silently with bare `pass`.** The comment claimed it was safe because "the 95% alert catches drift" — but that alert fires atomically once at threshold-crossing per Q1's design; a Redis outage during a burst of failed mints would accumulate quota drift with zero observable signal. Fix: replaced bare `pass` with `swallow_log(_logger, "tenant_quota_release_failed", exc, ...)` so the shared `EXCEPTION_SWALLOWED_TOTAL{event="tenant_quota_release_failed"}` counter fires and ops can page on clustered rollback failures.

- **`services/witness/analytics.py::aggregate` had no input size limit** — DoS surface at `/witness/analytics`. Streaming millions of records → four unbounded `defaultdict`s → OOM. Fix: `_MAX_RECORDS = os.getenv("WITNESS_ANALYTICS_MAX_RECORDS", "250000")` counted in the iteration loop with early abort via new `AnalyticsInputTooLarge`. Router surfaces 413 (not generic 500 — ops distinguishes cap-hit from other failures). Iteration-time check means even an infinite generator gets bounded. **5 new tests**: under-cap OK, at-cap OK, over-cap raises with correct message, default cap large enough for §12.1 workloads (~150K verdicts/day), infinite-generator aborted at ceiling.

**Suite: 1974 pass / 0 fail / ruff clean.**

**Q11 — Two more real bugs fixed:**

- **MCP gate audit trail persisted userinfo-in-URL credentials.** Operators sometimes configure downstream MCP servers as `https://user:pass@host/path` for legacy servers. The gate's audit event embedded `downstream_url` verbatim → credential lived in the ledger + every export bundle forever. Fix: `_redact_url_credentials` strips userinfo from the netloc via `urlparse`/`urlunparse` before metadata is written. Every other URL component (host, port, path, query, fragment) preserved. **9 new tests**: basic-auth stripped, user-only stripped, no-credentials unchanged, port preserved, path/query/fragment preserved, `@` in path not confused for userinfo, non-URL passthrough, http scheme also redacted, empty-password still stripped.

- **Lifecycle `/lifecycle/transition` returned 500 on malformed body.** `await request.json()` raised `ValueError` on non-JSON input → uncaught 500 → alert page for a legitimate 400. Fix: wrapped `.json()` in try/except with clean 400; added `isinstance(body, dict)` gate (a JSON array top-level was allowed through and would raise `AttributeError` on `.get()`); added `if not target` guard so empty target returns 400 not 500. **5 new tests**: non-JSON, non-object, missing target, empty target, missing tenant context.

**Suite: 1969 pass / 0 fail / ruff clean.**

**Q10 — Two more real bugs fixed, both robustness-under-failure:**

- **Witness signer key-persist was non-atomic.** Write-then-chmod meant a partial write on disk-full, power loss, or SIGKILL could leave a corrupted PEM. Next boot would fail-LOUD (which was intentional — but the loud failure in production means **refuse-to-boot** because `_resolve_key` raises. So a disk-full during rotation would take the witness offline until manual intervention). Fix: rewrote `_generate_and_persist` to write to `signing.pem.tmp.<pid>` + `fsync` fd + chmod 0600 + `os.rename` (POSIX-atomic within one filesystem) + `fsync` parent directory for durability. A crash mid-persist leaves either the OLD key intact or nothing — never a corrupted target. **3 new tests**: no tmp file left after success, old key intact after simulated crash mid-rename, post-rename mode 0600.

- **Lifecycle transition audit/state ordering allowed silent state drift.** `_store(new_state)` ran BEFORE `push_audit_event(...)`. If audit push failed (Redis stream backpressure, network blip), the state changed but no ledger event fired → silent drift only visible on next `GET /lifecycle`. Fix: swapped the order — audit event first, state store second. Audit failure now keeps state at `current` and returns 500 (retryable). If the state store fails after a successful audit, we have an "event stronger than reality" — a strictly lesser evil (auditor sees a phantom transition and can reconcile via a compensating C3 rollback event; the reverse is undetectable state drift).

**Suite: 1955 pass / 0 fail / ruff clean.**

**Q9 — Two more real bugs fixed, both DoS/leak surfaces:**

- **Tenant quota slot leaked on `service.create_agent` DB failure.** The Redis INCR happened first, then the DB write. If the DB raised (duplicate name, connection loss, invariant failure), the counter stayed high — permanent quota leak per failed mint. Fix: wrapped the DB call + downstream steps in a try/except that calls `_release_quota()` before re-raising. The `_release_quota()` helper is idempotent (only DECRs if quota reservation actually happened, `_quota_status == "ok"`). Every exit path from the handler is now quota-symmetric.

- **MCP gate downstream-response-size DoS.** The gate loaded the entire downstream response into memory via `resp.json()`. A hostile or misconfigured downstream could stream 10 GB and OOM the worker before we ever touched the body. Fix: rewrote `_forward_to_downstream` to use `httpx.AsyncClient.stream("POST", ...)` with a running byte-total check against `MCP_GATE_MAX_RESP_BYTES` (default 16 MiB). Two enforcement layers: (a) Content-Length pre-check when downstream is honest, (b) per-chunk running-total check that aborts mid-stream when downstream lies or omits the header. New `DownstreamResponseTooLarge` exception surfaces as JSON-RPC 502. **5 new tests** covering small-response passthrough, honest-oversize rejection, lying-Content-Length mid-stream abort, absent-header still-bounded, exact-boundary allowed.

**Suite: 1952 pass / 0 fail / ruff clean.**

**Q8 — SCIM client URL construction had path-traversal + query-injection surface.**

`ScimClient.lookup_user` built the endpoint URL via `f"{base}/Users/{user_id}"` — no validation, no encoding. A hostile directory or a malformed overlay ref could inject `../../admin` (traversal), `u_1?admin=1` (query smuggling), or `u_1#…` (fragment). Real gap even though the SCIM directory is nominally trusted — enterprise threat models include partial compromise of the identity provider.

Fix: two layers, defense-in-depth:
- `_is_safe_scim_id` — strict allow-list regex `^[A-Za-z0-9_\-:]{1,256}$` gate BEFORE URL construction. Rejects `.`, `/`, `?`, `#`, whitespace, unicode. Refs that fail return `NOT_FOUND` without hitting HTTP.
- `urllib.parse.quote(safe='')` on the id even after the regex passes — every non-alnum char percent-encoded so the URL is unambiguous.

**7 new tests** in `sdk/common/tests/test_scim_client.py::TestSanitization`: path-traversal rejected, query-string rejected, fragment rejected, whitespace rejected, dot rejected (single dot could start a `.env`/`..` sequence), UUID ids still resolve, 257-char id rejected as too long (unbounded-log defense).

**Suite: 1947 pass / 0 fail / ruff clean.**

**Q7 — Two more real bugs found + fixed:**

- **C3 sampling would BLOCK every C3 call** — the planner fingerprinted the full Anthropic `tool_use` payload including the per-call `id` (`toolu_...`). Anthropic mints a fresh id every call, so 3 semantically-identical samples always registered as INCONSISTENT → BLOCK on 100% of C3 traffic. The C3 sampling feature was worse than useless — it was a denial-of-service on high-value calls. Fix: fingerprint on `{name, input}` only, drop the id. **2 new tests** in `services/gateway/tests/test_c3_planner_fingerprint.py`: same-plan-different-ids → CONSISTENT + ALLOW; diverging-inputs → BLOCK.
- **MCP gate had no downstream auth model** — the gate correctly stripped the agent's inbound bearer before forwarding (verified with a regression test in this same pass), but had no way to authenticate the gate → downstream MCP call when the customer's server sits behind a bearer-auth proxy. Real deployment blocker. Added optional `MCP_GATE_DOWNSTREAM_BEARER_TOKEN` env var that injects `Authorization: Bearer <token>` on outbound calls only when set. **4 new tests** in `services/mcp_gate/tests/test_downstream_auth.py`: (a) inbound bearer never leaks to downstream, (b) downstream bearer injected when configured, (c) absent when unset, (d) **caller-headers dict not mutated** (subtle retry-safety bug where a mutated dict would leak the downstream secret to unrelated retries).

**Suite: 1940 pass / 0 fail / ruff clean.**

**Q6 — Four more real bugs in my OWN recent code, all fixed:**
- **MCP gate body-size DoS surface** — no body limit; one huge POST could exhaust worker memory at the bearer boundary. Added `MCP_GATE_MAX_BODY_BYTES=1 MiB` (env-tunable), checked at BOTH Content-Length (pre-read) AND actual body length (post-read) so lying clients don't slip past. Returns 413.
- **MCP gate accepted unknown policy tiers unchecked** — `tier="Allow"` or a typo would have collapsed to deny (safe) but silently. Now validated against `{allow, monitor, escalate, deny, quarantine}` — unknown → explicit `deny` + WARN log with raw tier truncated for audit.
- **Witness fetch silently skipped corrupted entries** — an attacker with Redis write access (or code-level model drift) could hide real observations under garbage. Replaced plain `logger.warning` with `swallow_log(..., "witness_obs_parse_failed", ...)` so the `EXCEPTION_SWALLOWED_TOTAL` counter fires + ops alerts can page.
- **C3 pre-classifier substring false-positives** — `pay in "get_pay_history"` matched, over-classifying READ tools as C3 and burning the 3× sampling budget on innocent calls. Rewrote with (a) word-boundary regex, (b) tenant-configurable extra tokens `ACP_C3_EXTRA_TOOL_TOKENS`, (c) **read-verb-prefix exemption** (`get_`, `list_`, `check_`, `describe_`, `show_`, `read_`, `fetch_`, `is_`, `has_`, `count_`, `undelete_`, `restore_`, `recover_`, `info_`, `stat_`, `status_`, `query_`) — any tool with these prefixes is C2 even if a destructive token appears later. **6 new regression tests** covering `get_pay_history` (read), `undelete_backup` (read), `dropbox_client_list` (`dropbox` is one token), `terminate_session` (C3), `wire-transfer-funds` (C3), `iac_destroy` (C3).

**Suite: 1934 pass / 0 fail / ruff clean.**

**Q5 — Two real security bugs in my OWN recent code, caught + fixed:**

- **MCP gate auth model broken by design.** I had put `verify_internal_secret` (mesh JWT required) on the router, but the intended caller is the AGENT RUNTIME co-located with the gate per ATF §5.1. Agents don't have mesh JWTs — the whole service was unusable. **Fix**: new `verify_mcp_bearer` dependency using `MCP_GATE_BEARER_TOKEN` env var with `hmac.compare_digest` constant-time check + uniform `Unauthorized` error + `WWW-Authenticate: Bearer realm="aegis-mcp-gate"`. Production **refuses to boot** without the token set. Dev generates + logs an ephemeral one. **10 new tests** in `services/mcp_gate/tests/test_bearer_auth.py` — prod refusal, dev generation, wrong-scheme/empty/missing/correct-token, no-oracle uniform error shape.

- **Witness signer ephemeral-per-boot.** Every restart rotated the attestation fingerprint. Verifiers caching the previous fingerprint would fail across container recycles → cascading auditor rejections. **Fix**: rewrote `_resolve_key` with proper precedence — `WITNESS_SIGNING_KEY_PEM` env var > `WITNESS_SIGNING_KEY_PATH` file (auto-generated + persisted mode `0600`) > prod refuses to boot > dev ephemeral with WARN. Corrupted PEMs raise `ValueError` (not silently regenerated); RSA keys where Ed25519 expected raise. Compose adds `witness_keys` volume at `/data/keys/`. **9 new tests** including "fingerprint stable across restarts" — the whole point of the fix.

**Suite: 1928 pass / 0 fail / ruff clean.**

**Q4 — Four previously-deferred items shipped as real code:**
- **Witness store → Redis** — per-gate TTL + flood cap via atomic `RPUSH+LTRIM+EXPIRE` pipeline; explicit memory fallback surfaced on `/witness/health`. Multi-worker containers now share evidence + restarts survive. 8 real tests including 50-task concurrent-write consistency.
- **MCP server-side proxy (ATF §5.1)** — new `services/mcp_gate/` microservice: `POST /mcp/messages` parses JSON-RPC, gates every `tools/call` via `/policy/evaluate`, forwards to `MCP_GATE_DOWNSTREAM_URL` only on allow, ledgers every call. Fails CLOSED on policy unreachable, SSRF-guards the downstream URL, `follow_redirects=False`, blank env → 503 not silent forward. Container at `127.0.0.1:8018` in compose. **27 real tests** covering every parse-error path + deny/escalate/quarantine JSON-RPC shapes.
- **§12.1 benchmark harness** — `tests/load/atf_reference_workload.py` Locust harness with the exact class mix (85/10/4.5/0.5), delegation + escalation probabilities, `workload_id="atf_ref_v3_2_2026_07_22"` cited on every measurement. Runner script `scripts/bench/run_atf_reference.sh`. Class distribution verified 85.1/9.9/4.5/0.5 over 100k draws.
- **Ponytail-debt ledger** — 3 markers → 1 (the one that's a real 10k-agent trigger). Witness `swap-to-Redis` marker retired (done). Allowlist self-check marker removed (not a deferral).

**Suite: 1909 pass / 0 fail / ruff clean.**

**Q3 — C3 pre-classifier made the sampling wire live.**
The C3 sampling hook in `messages.py` was gated on hardcoded `_pre_classify_c3 = False`. Replaced with real `_classify_incoming_anthropic_request(raw_body)` that peeks `tools[]` for payment/transfer/wire/delete/drop/destroy/terminate/quarantine/iac_destroy/kubectl_delete patterns → C3.
- 12 tests in `services/gateway/tests/test_pre_classify.py`.
- Suite: **1874 pass / 0 fail / ruff clean.**
Security-review pass on W2 found a real TOCTOU race: the original read-then-INCR sequence let two concurrent create_agent calls at count == quota-1 both succeed. Fixed with `services/registry/quota_atomic.py::_QUOTA_RESERVE_LUA` — atomic Redis EVAL that reads, checks against ceiling, and INCRs in one op (same pattern as `proxy_helpers._RESERVE_LUA` from S7 / P1-6). Also emits the 95%-headroom alert EXACTLY ONCE — on the mint that crosses the boundary — not once per mint above.
- 7 new tests in `services/registry/tests/test_quota_atomic.py`:
  - Under-cap OK, exceeded-at-cap refuses without INCR, alert-on-crossing-only.
  - **Concurrency proof**: 50 tasks racing at cap-1 → exactly 1 'ok' + 49 'exceeded'; final counter == cap, zero over-shoot.
  - Release decrement, Redis failure → 'err'.
  - Cross-validates `_headroom_threshold` matches `evaluate_mint` semantics across 5 cap sizes.
- Suite: **1862 pass / 0 fail / ruff clean.**

## Honest wiring status after 2026-07-21 pm integration pass

**13 modules WIRED (reachable at runtime):**

| Module | Consumer |
|---|---|
| `sdk.common.atf_class` | `services/gateway/routers/messages.py` + `openai_messages.py` (action_class in every audit row) |
| `sdk.common.atf_state` | `services/witness/auto_lockout.py` |
| `sdk.common.atf_entry` | `GET /logs/{audit_id}/atf-view` |
| `sdk.common.aegis_profile` | Agent creation (`services/registry/router.py::create_agent`) writes profile_hash into audit |
| `sdk.common.atf_lifecycle` | `services/gateway/routers/lifecycle.py` — `GET /lifecycle`, `POST /lifecycle/transition` |
| `sdk.common.atf_article_12` | `GET /compliance/article-12-mapping` |
| `sdk.common.gate_mode` | `/status` surfaces `gate_mode: enforce|dry_run|shadow` |
| `services/witness/analytics.py` | `POST /witness/analytics` |
| `services/witness/auto_lockout.py` | `POST /witness/lockout` |
| `services/witness/reconciliation.py` | `POST /witness/reconcile` |
| `services/witness` (service) | `infra/docker-compose.yml` → container on port 8017 |
| `services/gateway/routers/witness_proxy.py` | Gateway `/witness/*` forwards to the container |
| `services/policy/manifest.py` | `_log_audit` stamps `policy_manifest_hash` into every decision |

**8 modules library-only (honestly gated):**

| Module | Why not wired |
|---|---|
| `sdk.common.jcs_check` | CI conformance check, correct as-is |
| `sdk.common.atf_export_bundle` | v3 export format — audit-side export rewrite is a separate sprint |
| `sdk.common.consistency_sampling` | Needs a C3 planner shim — no planner in Phase 1 codebase |
| `sdk.common.spiffe_auth` | Needs a real SPIRE trust bundle to end-to-end test |
| `sdk.common.entra_auth` | Needs an Entra tenant + Conditional Access setup |
| `sdk.common.okta_xaa` | Needs an Okta agent app + XAA config |
| `sdk.common.tenant_quota` | No profile-mint counter flow exists yet; adding one is separate registry surgery |
| `sdk.common.behavior_opt_in` | Feature-flag helper; guards a feature not yet promoted |
| `services/policy/policy_test_runner.py` | CI-shaped library, correct as-is |
| `services/policy/scim_agent.py` | Needs a real SCIM client wired at deploy time |
| `services/autonomy/escalation_adapters.py` | Existing `webhook_executor.py` handles the deployed channels; wiring Teams/PagerDuty/email needs channel-selector work |
| `services/identity_graph/collusion.py` | Needs an identity_graph subscriber calling it — separate integration |

## ATF v3.2 roadmap — Phase 1 (kernel)

### Phase 1a — Kernel Scaffolding (roadmap §Phase 1a)
- [x] **C0-C3 classification predicate** — `sdk/common/atf_class.py` (§3.3, deterministic, fail-toward-scrutiny, self-checked).
- [x] **4-state agent machine** — `sdk/common/atf_state.py` (VERIFIED/RESTRICTED/QUARANTINED/UNKNOWN per §9.1, pure derivation function).
- [x] **§7.1 ledger entry shape** — `sdk/common/atf_entry.py` adapter over existing `audit_logs` row → intent/authorization/observation/outcome/chain quads; `entry_version` + `is_supported_major` for §7.4 MAJOR gating.
- [x] **RFC 8785 JCS conformance** — `sdk/common/jcs_check.py` verifies existing `canonical_json` matches JCS on the §7.1 domain vectors; deviation limits documented.
- [x] **Response payload hash in outcome** — added `response_hash` + `action_class: C2` at both LLM proxy sites (`services/gateway/routers/messages.py`, `openai_messages.py`).
- [x] **Policy manifest hash in every entry** — `services/policy/manifest.py` hashes all `.rego` files at import; stamped into `_log_audit` metadata as `policy_manifest_hash`.
- [x] **Aegis Profile document (§4.3)** — `sdk/common/aegis_profile.py` with subject/human_responsible/provenance/gate_policy_ref/action_class_max + canonical fingerprint.
- [x] **P1-17 destructive-action rego** — already shipped in earlier sprint (`_are_destructive`).

### Phase 1b — Execution Witness (roadmap §Phase 1b — the wedge)
- [x] **Witness service skeleton** — `services/witness/{__init__,main,router,schemas,verdict,signer,store}.py` + 7 unit tests. Endpoints: `POST /witness/observations`, `POST /witness/heartbeat/{id}`, `POST /witness/verdict`, `GET /witness/health`, `GET /witness/public-key`.
- [x] **Verdict engine** — `services/witness/verdict.py`: CORROBORATED / CONTRADICTED / UNOBSERVED, pure function, degraded-mode + expected-evidence + non-2xx contradiction rules.
- [x] **Ed25519 attestation signing** — `services/witness/signer.py` (env-driven key, RFC 8785 canonical body).
- [x] **Heartbeat + `UNOBSERVED` on staleness** — `_HEARTBEAT_STALE_SECONDS = 30`; missing beat → all verdicts flip to UNOBSERVED.
- [ ] **eBPF sidecar probes** — deferred (ops-side infrastructure, per-platform). The service consumes probe-shaped events; probe implementation is Ops.
- [x] **Auto-lockout on CONTRADICTED → QUARANTINED** — `services/witness/auto_lockout.py`: pure `apply_verdict()` → `StateChange | None`; caller (subscriber) persists via registry + emits C2 ledger row. Transition-explaining reason string included.
- [x] **Reconciliation SLO cron (logic)** — `services/witness/reconciliation.py`: pure `reconcile()` produces `ReconciliationReport`; `synthesize_unobserved_verdicts()` turns each C2/C3 gap into an explicit UNOBSERVED entry so gaps are visible in the export (I1). Scheduler wiring itself remains ops.

### Phase 1c — Kernel finishing
- [x] **Deployment lifecycle state machine** — `sdk/common/atf_lifecycle.py` (INSTALL→BOOTSTRAP→ENFORCE→ROTATE/UPGRADE/ROLLBACK→DECOMMISSION→DESTROY) with legal-transition table + IllegalTransition.
- [x] **Export bundle format v3** — `sdk/common/atf_export_bundle.py` (§7.3 shape, §7.4 semver + `UnsupportedBundleVersion`, deterministic `bundle_digest`).
- [x] **Article 12 mapping artifact** — `sdk/common/atf_article_12.py` (8 requirements → kernel component → endpoint pointer, ready for compliance router).
- [x] **Egress lockdown checker** — `scripts/ops/verify_egress_lockdown.py` (Kubernetes NetworkPolicy / AWS security-group / iptables firewall shapes; LOCKED_DOWN / OPEN / HUMAN_REVIEW verdict + JSON output).
- [x] **Dry-run gate mode primitive** — `sdk/common/gate_mode.py` (env-driven `enforce`/`dry_run`/`shadow`; `apply_mode_to_decision` rewrites deny/escalate → allow under dry_run for §5.4 baseline runs).
- [ ] **MCP server-side proxy** — deferred (structural refactor of `services/mcp_server/` from client-side to server-side; larger scope, keep as dedicated sprint).
- [ ] **Anchor cross-signing verification on rotation** — verify existing `scripts/maintenance/rotate_transparency_key.py` in a live rotation drill; ops task.
- [ ] **RFC 3161 timestamping authority backend** — deferred (external TSA credentials + integration test).

### Phase 2a — Product layer
- [x] **Policy testing suite (§5.4)** — `services/policy/policy_test_runner.py`: `run_unit_assertions` (bundles without passing assertions can't reach PUBLISHED), `replay` (candidate policy vs historical entries, divergence report), `coverage` (fire counts + dead rules + default-only ratio). UI is separate work.
- [x] **SCIM `human_responsible` reconciler** — `services/policy/scim_agent.py`: pure `reconcile()` over an agent iterable + `scim_lookup` callable; ACTIVE/SUSPENDED/NOT_FOUND → OK/QUARANTINE/RESTORE; SCIM transient outage does NOT mass-quarantine.
- [x] **Escalation channel adapters (§5.7 + D.2)** — `services/autonomy/escalation_adapters.py`: MS Teams Adaptive Card, PagerDuty Events API v2, email (subj/body), canonical webhook body. Slack/Jira/ServiceNow already exist in `webhook_executor.py`.
- [x] **Consistency Sampling for C3 (§9.3)** — `sdk/common/consistency_sampling.py`: 2/3 quorum plan-fingerprint check, distinguishes CONSISTENT / INCONSISTENT / NEEDS_HUMAN. 3× cost budgeted, C3-only.
- [ ] **RFC 3161 anchoring** — deferred (external TSA credentials).
- [ ] **Benchmark publication (§12.2)** — YAGNI until a real reference-workload run replaces the `TBD` cells.

### Phase 2b — Identity consumption depth (§4.2)
- [x] **SPIFFE SVID verifier** — `sdk/common/spiffe_auth.py`: JWKS-shaped trust bundle, `parse_spiffe_id`, uniform `SpiffeVerifyError`, trust-domain gate.
- [x] **Entra Agent ID verifier** — `sdk/common/entra_auth.py`: `login.microsoftonline.com/{tid}/v2.0` issuer, JWKS-loader callable, `verify_aud=True`, `tid` gate.
- [x] **Okta XAA + RFC 8693 token exchange** — `sdk/common/okta_xaa.py`: `verify()` + `build_exchange_request()` + `parse_exchange_response()`; only accepts `issued_token_type = urn:ietf:params:oauth:token-type:jwt`.
- [x] **Tenant issuance quota (§4.4)** — `sdk/common/tenant_quota.py`: `evaluate_mint` + `enforce_mint`, C2-ledger flag at 95% headroom, blocks past ceiling.
- [ ] **IETF AIMS composer** — YAGNI until the draft advances beyond `draft-klrc-aiagent-auth-00`.

### Phase 3 — Analytics (data-independent pieces landed)
- [x] **Contradiction analytics aggregator** — `services/witness/analytics.py`: `aggregate()` produces per-tenant / per-agent / per-tool `RateStats` + SOC triage ranking (score = contradicted × (1 + unobserved_ratio)); `top_offending_tools()` for the heat map.
- [x] **Per-tenant interaction graph collusion detection** — `services/identity_graph/collusion.py`: `label_propagation_communities()` (Raghavan 2007 LPA, deterministic, weight-thresholded) + `taint_propagate()` (BFS from a seed set of "known-bad" agents, hop-limited, weight-thresholded). Louvain remains the upgrade path when a tenant crosses ~10k nodes.
- [x] **Behavioural fingerprinting opt-in gate** — `sdk/common/behavior_opt_in.py`: `ACP_BEHAVIOR_FINGERPRINTING_TENANTS` allow-list + `_MODE` flag; `gate_score_consumption(..., "gate_input")` ALWAYS refused per ADR-002 (never authoritative).
- [ ] **Learned trust fusion** — honestly deferred per §9.2. Requires ≥ 6 months of production ledger data, ≥ 1 confirmed incident class, and a red-team run on the corpus. Cannot ship on nothing.

### Phase 4 — Ecosystem (spec publication)
- [x] **Publish ledger entry + attestation + export bundle as spec** — `specs/`:
  - `ledger_entry.schema.json` (§7.1 + §7.4)
  - `witness_attestation.schema.json` (§6.4)
  - `export_bundle.schema.json` (§7.3 + §7.4)
  - `README.md` — semver + canonicalization + third-party integration guide
  - `verify_schemas.py` — cross-checks that the reference Python impl matches every published schema
- [ ] **AIMS / NCCoE alignment** — pending draft-klrc-aiagent-auth-00 advancement + NCCoE demonstration project deliverables.
- [ ] **Consortium anchoring** — gated at ≥ 10 tenants demanding it (§ATF v3.2 §13 explicit exclusion until then).
