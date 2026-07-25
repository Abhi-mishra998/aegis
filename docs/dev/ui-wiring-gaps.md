# UI Wiring Gaps — Honest Ledger

**Date:** 2026-07-25  (Sprint UI-1 closed)
**Purpose:** After a long backend session (Q1–Q42, W1–W8, Category B, §14.5 lifecycle), some newly-shipped backend features do NOT yet have a UI consumer. An enterprise customer cannot reach them from the app.

This file is the honest gap list. Do not close an item until UI ships **and** an OMEGA-review agent verifies the flow end-to-end.

Legend: 🔴 blocker for enterprise self-serve · 🟡 admin/ops workflow · 🟢 platform-team-only · ✅ shipped

## Sprint status

| Sprint | Items | Status |
|---|---|---|
| UI-1 | ATF v3 button · SCIM reconcile · witness trust doc · approval scope | ✅ shipped |
| UI-2 | provenance block · witness deployment-mode · signing-key history · issuance quota | ✅ shipped |
| UI-3 | C3 toggle · behavior opt-in · collusion feed | ✅ shipped |
| UI-4 | deployment lifecycle page · destruction-cert flow | ✅ shipped |
| UI-5 | multi-IdP config · Teams/PagerDuty channels | ✅ shipped |
| UI-6 | detection engine summary/history/timeline/top-threats + signal-weights tuning | ✅ shipped |

---

## 🔴 Blockers for enterprise self-serve

| # | Backend feature | Endpoint(s) | UI needed | Notes |
|---|---|---|---|---|
| ~~1~~ ✅ | ATF v3 export bundle (W3) | `GET /audit/logs/export-atf-v3` | ✅ "ATF v3 bundle" button shipped on `pages/Compliance.jsx` + gateway proxy added | Sprint UI-1 |
| ~~2~~ ✅ | Destruction certificate (Q24) | `POST /audit/logs/destruction-certificate` | ✅ Downloadable certificate card on `pages/LifecycleAdmin.jsx`. DESTROY transition auto-attaches the cert in the response; re-issue button available while audit rows remain on disk | Sprint UI-4 |
| ~~3~~ ✅ | Deployment lifecycle (§14.5) | `GET /lifecycle`, `POST /lifecycle/transition` | ✅ New `pages/LifecycleAdmin.jsx` (route `/lifecycle`, Admin nav). Happy-path timeline + reachable-target buttons + ConfirmDialog with reason field + inline ledger of last 40 transitions. OWNER-only writes; 409 illegal-transition surfaced with actionable message. DESTROY uses danger variant + Skull icon | Sprint UI-4 |
| ~~4~~ ✅ | Multi-IdP dispatcher (W1) | `services/gateway/idp_verifiers.py`; new `GET /auth/idp/status` (read-only) surfaces enabled + identifier + audience per adapter | ✅ "Trusted issuers" panel shipped on `pages/SsoSettings.jsx`. Read-only by design (trust roots are deployment-wide; a compromised admin flipping SPIFFE_TRUST_BUNDLE_JSON via UI would let them nuke chain-of-trust). Empty adapters show the env-var names ops must set | Sprint UI-5 |
| ~~5~~ ✅ | SCIM reconciler trigger (W5) | `POST /scim/reconcile` | ✅ "Reconcile now" button shipped on `components/settings/ScimTokensTab.jsx` + gateway proxy added | Sprint UI-1 |
| ~~6~~ ✅ | Escalation channel selector (W6) | `fire_teams`, `fire_pagerduty`, `fire_generic_webhook` in `services/autonomy/webhook_executor.py`; per-tenant Redis config at `acp:webhooks:{tenant_id}` | ✅ Slack + PagerDuty + generic were already on `pages/WebhookSettings.jsx`; Sprint UI-5 added Teams: `teams_url` on WebhookConfig schema, `POST /webhooks/test/teams` endpoint, SEND_ALERT dispatch `channel: teams` branch in `webhook_executor.py`, IntegrationCard on WebhookSettings.jsx with adaptive-card test button | Sprint UI-5 |

## 🟡 Admin / ops workflows without UI

| # | Backend feature | Backend surface | UI needed | Notes |
|---|---|---|---|---|
| ~~7~~ ✅ | Tenant issuance quota (W2) | `POST /agents` returns `429 QUOTA_EXCEEDED` when tenant is at cap; C2 ledger event on approach | ✅ "Agents" tile shipped on QuotaManagement (profile_cap + profile_count added to `/tenant/quota` response); Agents.jsx create-agent catch shows a friendly quota-reached message pointing at QuotaManagement | Sprint UI-2 |
| ~~8~~ ✅ | Consistency sampling C3 (W4) | `ACP_C3_SAMPLING_TENANTS` env var + per-tenant Redis override (`sdk/common/tenant_settings.py`); `POST /tenant/settings` | ✅ "Feature flags" tab shipped on Settings page (`components/settings/FeatureFlagsTab.jsx`). OWNER-role gated, 60s cache, effective-vs-override surfaced. Gateway `messages.py` proxy now uses async form that consults the per-tenant flag before the historical env-var list | Sprint UI-3 |
| ~~9~~ ✅ | Behavior opt-in gate (W8) | `gate_score_consumption_async` at `services/behavior/service.py:313`; per-tenant Redis flag via `POST /tenant/settings` | ✅ Same "Feature flags" tab exposes `behavior_fingerprinting` toggle. `behavior/service.py` now calls the async form → learned cross-agent term is gated per-tenant; `advisory-only` invariant preserved (never authoritative, even with flag on) | Sprint UI-3 |
| ~~10~~ ✅ | Collusion detector (W7) | `services/identity_graph/worker.py::_collusion_loop` writes `signal_type="collusion_suspicion"` DriftSignals; surfaced via `GET /graph/drift?minutes=…` | ✅ New "Collusion" tab shipped on `pages/Incidents.jsx`. Groups drift signals by `observed.cluster_id`, shows elevated/cluster-size, member id chips, severity (critical/warn), Δ score, and recency. Empty state explains the periodic-detector cadence | Sprint UI-3 |
| ~~11~~ ✅ | Rotate cross-signing status (Q25) | `transparency_historical_keys.transition_*` columns; `verify_rotation_cross_signature` | ✅ "Signing keys (audit chain)" panel shipped on `pages/Compliance.jsx` (active key + historical rotations with cross-signed marker) | Sprint UI-2 |
| ~~12~~ ✅ | Provenance block (B2) | `AgentProfile.provenance` populated at agent mint from `AEGIS_*` CI env vars | ✅ "Provenance (§4.3 Aegis Profile snapshot)" section on `pages/AgentProfile.jsx`. Backend also updated: `registry/service.py::persist_profile_snapshot` now writes provenance + `aegis_profile_hash` onto `agent.metadata_data` so `GET /agents/{id}` surfaces them | Sprint UI-2 |
| ~~13~~ ✅ | Witness deployment mode (B1) | `WITNESS_DEPLOYMENT_MODE=sidecar\|serverless`; `GET /witness/health` surfaces `deployment_mode` | ✅ "Execution Witness" panel shipped on `pages/SystemHealth.jsx`. Sidecar → green + evidence-collected note; serverless → amber + "verdicts UNOBSERVED by design" note; unknown → neutral. Also surfaces heartbeat-stale flag when set | Sprint UI-2 |
| ~~14~~ ✅ | Witness trust-boundary doc (B4) | `docs/security/witness-trust-boundary.md` | ✅ Card + link shipped on `pages/TrustCenter.jsx` (still pending: link from the future Witness settings panel — Sprint UI-2) | Sprint UI-1 |
| ~~15~~ ✅ | Approval scope enforcement (Q37) | `_approved_rule_id` carried on re-queued incident; worker skips other rules with `approval_scope_skip` audit event | ✅ Amber scope-of-approval banner shipped on `pages/ApprovalInbox.jsx` (names the rule + explains §5.7 single-action-binding) | Sprint UI-1 |

## 🟡 Detection engine surfaces (Sprint UI-6, added post-hoc)

Found by a second-pass audit after the original ledger's 15 items were closed:
six decision/risk endpoints were gateway-proxied but had no UI caller. Closed
in Sprint UI-6.

| # | Backend feature | Endpoint(s) | UI needed | Notes |
|---|---|---|---|---|
| ~~19~~ ✅ | Decision engine summary | `GET /decision/summary` (proxied) — returns `{threats_blocked, high_risk_agents, total_requests, metrics:[{time,score}]}` | ✅ "Detection Engine" panel on `pages/SystemHealth.jsx` with four stat tiles + inline SVG 24h risk sparkline | Sprint UI-6 |
| ~~20~~ ✅ | Decision engine history | `GET /decision/history?limit=` (proxied via `routers/decision.py`) | ✅ "Recent decisions" list on same SystemHealth panel — colour-coded BLOCK/ESCALATE/log with tool+timestamp | Sprint UI-6 |
| ~~21~~ ✅ | Top threats feed | `GET /risk/top-threats?limit=` (proxied via `routers/risk.py`) | ✅ "Top threats" list on same panel — ranked, count per threat | Sprint UI-6 |
| ~~22~~ ✅ | Risk timeline | `GET /risk/timeline?days=` (proxied) | ✅ Rolled into the sparkline above; `decisionService.getTimeline()` also exposed for future dashboard tiles | Sprint UI-6 |
| ~~23~~ ✅ | Signal weights (read) | `GET /decision/signal-weights` (proxied as `/risk/signal-weights`) | ✅ "Signal weights" tab on Settings (`components/settings/SignalWeightsTab.jsx`). Range slider + numeric input for each of the 5 signals (inference/behavior/anomaly/cost/cross_agent) | Sprint UI-6 |
| ~~24~~ ✅ | Signal weights (write) | `PUT /decision/signal-weights` — was missing gateway proxy; Sprint UI-6 added `PUT /risk/signal-weights` in `routers/risk.py` | ✅ Save button on the same tab. ADMIN/SECURITY only (both backend + UI role-gated); "Reset to defaults" restores 1.0 across the board | Sprint UI-6 |

## 🟢 Platform-team-only (no per-customer UI needed)

| # | Feature | Reason no UI |
|---|---|---|
| 16 | Witness proxy internal endpoints (`/witness/verdict`, `/witness/heartbeat/{id}`, `/witness/observations`) | Agent-runtime SDK calls, not customer UI |
| 17 | MCP gate `POST /mcp/messages` | Agent-facing proxy, not customer UI |
| 18 | AdminConsole `/admin` route | Platform super-admin (tenant health across every customer); intentionally URL-only, no sidebar |

---

## Billing page cosmetic gap (verified 2026-07-25)

`ui/src/pages/Billing.jsx` reads six fields from the `/billing/summary`
response that the backend does not return: `roi_percent`,
`avg_calls_per_day`, `peak_hour`, `cost_per_call`, `current_cost_usd`,
`total_events`. The UI has defensive `??` fallbacks so nothing crashes —
each field just renders as `0` or `null`. That's misleading customer-
facing analytics, not a bug crash. Fix is either:
  (a) backend adds the fields to the summary response, or
  (b) UI drops the fields it can't populate from real data.

Prefer (a): if a customer sees an ROI number, it should be a real one.

---

## Backend-side "orphan" endpoints (304 total)

The wiring audit found 304 backend routes with no frontend caller. The
vast majority are legitimate internal-only surfaces (mesh JWT gated):
SCIM webhooks, billing event ingestion, compliance-framework internal
shims, Clerk webhook receivers, transparency-log sealers, etc. These
are correct as-is — they are called by services or by the customer's
own SDK, not by the Aegis app.

If a future audit wants to trim, the list is in the parallel-agent
report; nothing in it is urgent.
