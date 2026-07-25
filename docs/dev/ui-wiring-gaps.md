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
| UI-3 | C3 toggle · behavior opt-in · collusion feed | pending |
| UI-4 | deployment lifecycle page · destruction-cert flow | pending |
| UI-5 | multi-IdP config · Teams/PagerDuty channels | pending |

---

## 🔴 Blockers for enterprise self-serve

| # | Backend feature | Endpoint(s) | UI needed | Notes |
|---|---|---|---|---|
| ~~1~~ ✅ | ATF v3 export bundle (W3) | `GET /audit/logs/export-atf-v3` | ✅ "ATF v3 bundle" button shipped on `pages/Compliance.jsx` + gateway proxy added | Sprint UI-1 |
| 2 | Destruction certificate (Q24) | `POST /audit/logs/destruction-certificate` | Download button + confirm-consent flow on the DESTROY lifecycle transition modal | §14.5 mandates the customer keeps this cert forever |
| 3 | Deployment lifecycle (§14.5) | `GET /lifecycle`, `POST /lifecycle/transition` | New "Deployment lifecycle" admin page — INSTALL→BOOTSTRAP→ENFORCE→ROTATE→UPGRADE→ROLLBACK→DECOMMISSION→DESTROY state machine with per-transition confirm dialog and audit-trail view | OWNER-role gated; every transition is a C3 ledgered event |
| 4 | Multi-IdP dispatcher (W1) | Backend accepts SPIFFE / Entra Agent ID / Okta XAA tokens via `services/gateway/idp_verifiers.py`; config lives in env vars | `pages/SsoSettings.jsx` currently only handles single SAML/OIDC — needs a "trusted issuers" list (SPIFFE trust domain, Entra tenant, Okta audience) with per-provider enable toggle | Enterprise SSO customer can't configure their identity source |
| ~~5~~ ✅ | SCIM reconciler trigger (W5) | `POST /scim/reconcile` | ✅ "Reconcile now" button shipped on `components/settings/ScimTokensTab.jsx` + gateway proxy added | Sprint UI-1 |
| 6 | Escalation channel selector (W6) | `fire_teams`, `fire_pagerduty`, `fire_webhook` in `services/autonomy/webhook_executor.py` | `SlackApprovalsTab.jsx` exists; needs sibling tabs for Teams (webhook URL), PagerDuty (routing key), generic webhook | Enterprise customer on Teams/PagerDuty currently has no path to wire escalations |

## 🟡 Admin / ops workflows without UI

| # | Backend feature | Backend surface | UI needed | Notes |
|---|---|---|---|---|
| ~~7~~ ✅ | Tenant issuance quota (W2) | `POST /agents` returns `429 QUOTA_EXCEEDED` when tenant is at cap; C2 ledger event on approach | ✅ "Agents" tile shipped on QuotaManagement (profile_cap + profile_count added to `/tenant/quota` response); Agents.jsx create-agent catch shows a friendly quota-reached message pointing at QuotaManagement | Sprint UI-2 |
| 8 | Consistency sampling C3 (W4) | `ACP_C3_SAMPLING_TENANTS` env var | Admin toggle on Settings → Policies tab: "Enable 3× consistency sampling on C3 actions" with cost warning | Env-only opt-in is fine short-term; UI toggle needed before it's a customer-configurable feature |
| 9 | Behavior opt-in gate (W8) | `gate_score_consumption` at `services/behavior/service.py:307` gates learned signals | Settings → Privacy tab: per-tenant toggle "Enable learned behavior fingerprinting (advisory)" | ATF §9.2: must be off by default + advisory-only. UI needs to reflect that constraint |
| 10 | Collusion detector (W7) | `services/identity_graph/worker.py::_collusion_loop` fires alerts | Incidents feed / SOC dashboard needs a "collusion cluster detected" event card with member drill-down (each agent + its ledger events in the window) | Detector is running; alerts have nowhere to render |
| ~~11~~ ✅ | Rotate cross-signing status (Q25) | `transparency_historical_keys.transition_*` columns; `verify_rotation_cross_signature` | ✅ "Signing keys (audit chain)" panel shipped on `pages/Compliance.jsx` (active key + historical rotations with cross-signed marker) | Sprint UI-2 |
| ~~12~~ ✅ | Provenance block (B2) | `AgentProfile.provenance` populated at agent mint from `AEGIS_*` CI env vars | ✅ "Provenance (§4.3 Aegis Profile snapshot)" section on `pages/AgentProfile.jsx`. Backend also updated: `registry/service.py::persist_profile_snapshot` now writes provenance + `aegis_profile_hash` onto `agent.metadata_data` so `GET /agents/{id}` surfaces them | Sprint UI-2 |
| ~~13~~ ✅ | Witness deployment mode (B1) | `WITNESS_DEPLOYMENT_MODE=sidecar\|serverless`; `GET /witness/health` surfaces `deployment_mode` | ✅ "Execution Witness" panel shipped on `pages/SystemHealth.jsx`. Sidecar → green + evidence-collected note; serverless → amber + "verdicts UNOBSERVED by design" note; unknown → neutral. Also surfaces heartbeat-stale flag when set | Sprint UI-2 |
| ~~14~~ ✅ | Witness trust-boundary doc (B4) | `docs/security/witness-trust-boundary.md` | ✅ Card + link shipped on `pages/TrustCenter.jsx` (still pending: link from the future Witness settings panel — Sprint UI-2) | Sprint UI-1 |
| ~~15~~ ✅ | Approval scope enforcement (Q37) | `_approved_rule_id` carried on re-queued incident; worker skips other rules with `approval_scope_skip` audit event | ✅ Amber scope-of-approval banner shipped on `pages/ApprovalInbox.jsx` (names the rule + explains §5.7 single-action-binding) | Sprint UI-1 |

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
