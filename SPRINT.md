# SPRINT.md — Aegis Sprint Roadmap (locked 2026-06-15)

Translation layer between PRODUCT_PLAN.md (v2, locked) and ground-level work. One sprint = one shippable unit on `ha.aegisagent.in`. No sprint completes without:

1. Backend + UI + alembic (if schema touched) landed in one commit.
2. Unit tests green (`python3 -m pytest tests/test_<sprint>*.py -q`).
3. Bundle built via `scripts/ops/build_release_bundle.sh` + uploaded to S3.
4. ASG instance refresh succeeded.
5. Live smoke probes documented in this file under the sprint's "evidence" line.

> **Behavioural carry-over (from prior memory + founder rules)**
> - Never push without explicit "push it" in the same turn.
> - No Co-Authored-By: Claude on any commit.
> - Never delete a backend service. UI consolidates, backend stays.
> - Customer's LLM key never touches Aegis servers — SDK-on-endpoint only.
> - `ACP_AUTH_PROVIDER=both` stays — legacy HS256 path keeps agent `/execute` working.

---

## Status snapshot

| Sprint | Phase | Title | Status | Deployed | Evidence |
|---|---|---|---|---|---|
| 1 | 1 | Clerk self-serve signup + shadow mode + Role enum | ✅ DONE | commit 6b5b3a7 / ASG 21cd7092 | `/webhooks/clerk` 400 missing-svix; `/system/health` 12/12 |
| 2 | 2 | Agent Onboarding Wizard (3-step, no LLM key) | ✅ DONE | commit 8c21e16 / ASG 29475f7d | `/agents/wizard` 401 (route live); `/onboarding` 200 (Vite dist served); 105/105 tests; 12/12 healthy p95 38ms |
| 3 | 3.1 | Shadow Mode review surface + would_have_blocked middleware | ✅ DONE | commit e89a33b + 96c873c / ASG 4ca3b61d + 1e742e76 | `/workspace/me` 401 JSON; `/workspace/exit-shadow-mode` 401 JSON; nginx allow-list fixed; 117/117 tests; 12/12 healthy p95 40ms |
| 4 | 3.2 | Dashboard landing (Agent Inventory hero) | ✅ DONE | commits 69c0794 + 037da84 / ASGs fdd95c15 + fe7c8c27 | Hotfix verified: new bundle hash served (DdWNCPBK), `/workspace/inventory` 401 JSON, `/dashboard` 200, "medium" tier in bundle, 124/124 tests, 12/12 healthy p95 34ms. Took 2 deploys — the first shipped a runtime bug in Dashboard.jsx that smoke probes missed. |
| 5 | 3.4 | Incidents enriched (blast radius + remediation + forensics tabs) | ✅ DONE | commit 7815d7b / ASG 33c463f9 | All 4 orphan endpoints 401 JSON (`/iag/incidents/.../blast-radius`, `/remediation/policy`, `/remediation/incidents/...`, `/forensics/blast-radius/...`); bundle hash flipped to BDU7gfyT; "Blast Radius" + "Remediation" + "would_have_blocked" strings all present in bundle; 124/124 tests; 12/12 healthy p95 37ms |
| 6 | 3 cleanup | UI consolidation: 49→15 pages, sidebar restructure | 🚧 IN-FLIGHT | committed locally | 124/124 tests; deploy pending; bundle dropped 1.72MB→1.58MB via tab lazy-load |
| 7 | 5 | Threat Graph (`/threat-graph` + MitreCoverageGrid) | ⏳ | — | — |
| 8 | 5 | Blast Radius dollar formula + workspace value tags | ⏳ | — | — |
| 9 | 6 | Stripe billing wiring (model exists, wire it) | ⏳ | — | — |
| 10 | 6 | Production hardening: CSP, security headers, audit-chain refresh | ⏳ | — | — |

Phase 4 (3 pilots) is calendar work, not code — outside this file's scope.

---

## Sprint 2 — Agent Onboarding Wizard

**Goal** Customer clicks `+ Add Agent` → picks integration → names it → presses **Generate Aegis Key** → copies SDK snippet → runs SDK → first decision arrives. Customer's LLM key stays on their machine (PRODUCT_PLAN.md §1.3 is non-negotiable).

**Files**
- Backend
  - `services/registry/router.py` — `POST /agents/wizard` (composes create + whitelist standard 8 tools + mint `acp_…` key in one call behind customer JWT). `GET /agents/wizard/install-snippet/{agent_id}/{provider}` — returns SDK-specific copy-paste block.
  - `services/registry/service.py` — `create_agent_with_defaults(workspace, name, provider, risk_level)`.
  - `services/registry/alembic/versions/<new>.py` — `agents.metadata.provider` column (already JSONB? confirm and migrate accordingly).
  - `services/gateway/routers/agents.py` — thin proxy for `/agents/wizard*`.
- Frontend
  - `ui/src/pages/OnboardingWizard.jsx` (NEW, 3 steps) — pick integration, name + risk, install snippet + "waiting" SSE panel. **No LLM-key field — call this out in the UI.**
  - `ui/src/pages/Agents.jsx` — replace Deploy button with `Link to=/onboarding`.
  - `ui/src/App.jsx` — `/onboarding` (protected route).
  - `ui/src/services/agentService.js` — `wizard()`, `installSnippet()`.

**DoD**
- A signed-in OWNER creates their first agent + receives a working `acp_…` key in **< 60 s**.
- SDK snippet for Anthropic, OpenAI, Bedrock, LangChain, Cursor, Claude Code, OpenHands, Custom — 8 variants, pre-filled with `tenant_id`, `agent_id`, `aegis_api_key`. No `ANTHROPIC_API_KEY` placeholder is filled — the snippet shows the env var line as "keep on YOUR machine".
- Wizard's last step subscribes to `/events/stream` and auto-flips to "✅ First decision received" when an `/execute` lands for that agent_id.

**Smoke probes (post-deploy)**
- `curl -X POST https://ha.aegisagent.in/agents/wizard` (with Clerk Bearer) — expect 201 + `{agent_id, aegis_api_key, install_snippet}`.
- `curl https://ha.aegisagent.in/agents/wizard/install-snippet/<id>/anthropic` — expect 200 + Python snippet, no Anthropic key inside.
- Browser flow: signup → click + Add Agent → snippet renders → run `aegis-anthropic` locally with the printed values → `/execute` lands → wizard flips to ✅.

**Estimate** 5 dev-day spec; aim for one session.

---

## Sprint 3 — Shadow Mode Review + Middleware Downgrade

**Goal** Default 14-day shadow window already lives on `tenants.shadow_mode_until` (Sprint 1 migration). Now: middleware downgrade + review surface.

**Files**
- Backend
  - `services/gateway/middleware.py` — in the deny/escalate path: if `workspace.shadow_mode_until > now()`, downgrade to an audited `would_have_blocked` 200 with annotation. Add new SSE event type `would_have_blocked` in `_publish_event` switch.
  - `services/identity/router.py` — `POST /workspace/exit-shadow-mode` (`Depends(verify_role(Role.OWNER))`).
- Frontend
  - `ui/src/pages/ShadowModeReview.jsx` (NEW) — list of `would_have_blocked` events: ts / agent / tool / args excerpt / policy_id / MITRE technique; per-row **Confirm Block** vs **Allow-list**; bulk action toolbar.
  - Dashboard widget (lands in Sprint 4 but reserve hook here).

**DoD**
- Identity DB shows `shadow_mode_until > now()` on every workspace (Sprint 1 default). Middleware verified to NOT actually block when the window is open. Audit row carries `decision="would_have_blocked"` + `original_decision` so the review screen has data.
- `/shadow-review` shows last 7 days of would-have-blocked decisions for the signed-in workspace.

---

## Sprint 4 — Dashboard (Agent Inventory + Hero Metrics)

**Goal** Replace `/flight-recorder` as `/` landing with `/dashboard`.

**Files**
- Backend
  - `services/registry/router.py` — `GET /workspace/inventory` aggregator (agents grouped by provider + risk level + last-24h decision count).
- Frontend
  - `ui/src/pages/Dashboard.jsx` (NEW) — hero card (agent counts by provider + risk-tier) + open incidents tile + shadow widget + risk-trend sparkline + recent insights list.
  - `ui/src/components/dashboard/AgentInventoryHero.jsx`, `HeroMetricsCard.jsx`, `RiskTrendSparkline.jsx`.
  - `ui/src/App.jsx` — `/` → `/dashboard`; `/dashboard` → Dashboard.jsx (not FlightRecorder).

**DoD**
- Owner can answer "how many agents do we have, what are they doing, what risks are showing up" in 10 seconds.
- Existing FlightRecorder reachable at `/audit-feed` for analysts.

---

## Sprint 5 — Incidents Enriched (Blast Radius + Remediation + Forensics)

**Goal** Surface the orphan endpoints (`/iag`, `/remediation`, `/forensics`) via the Incidents detail drawer — these were built in Sprint 4/5/6 of the prior security track and have no UI consumer today.

**Files**
- Frontend
  - `ui/src/pages/Incidents.jsx` — add 3 panels per incident detail: Blast Radius (from `/iag/incidents/{id}/blast-radius`), Remediation policy + Replay (from `/remediation/policy` + `/remediation/incidents/{id}/replay`), Forensics quick-link (from `/forensics/blast-radius`).
  - `ui/src/components/incidents/BlastRadiusCard.jsx`, `RemediationPanel.jsx`, `ForensicsDrawer.jsx`.
- Backend — none. Endpoints already exist.

**DoD**
- Every incident shows blast radius + which remediation fired + a "Replay" button + a "Forensics" deep-link.

---

## Sprint 6 — UI Consolidation (49 → 15 pages)

**Goal** Execute the PRODUCT_PLAN §12 cleanup. Backend never touched (founder hard-rule). Sidebar restructured into 3-tier (primary 6 / advanced 10 / admin 4).

**Files (high-density change)**
- `ui/src/components/Layout/Sidebar.jsx` — rewrite nav into 3-tier.
- `ui/src/App.jsx` — trim routes 54→30; redirect `/executive` → `/dashboard`; redirect `/live-demo` → `/onboarding`; tab-route under `/policies`, `/agents/:id`, `/settings`, `/decision-explorer`.
- `ui/src/pages/Policies.jsx` (NEW tab router merging PolicyBuilder + PolicySim + PolicyPlayground + PolicyAnalytics + AutonomyContracts).
- `ui/src/pages/AgentSnapshot.jsx` (NEW tab router merging AgentProfile + AgentHealth + AgentCost + AgentTopology + IAG panel).
- `ui/src/pages/Settings.jsx` — 9 tabs lazy-loading existing pages.
- Delete `LiveDemo.jsx` + `Pricing.jsx` + `ExecutiveDashboard.jsx` (only 3 pages deleted; backend services preserved).

**DoD**
- Primary sidebar: Dashboard / Agents / Incidents / Live Feed / Policies / Settings (6 items + `g <letter>` hotkeys).
- Advanced + Admin tiers collapsed by default.
- All 27 backend services still mapped to a UI surface (§12.13 must remain accurate).

---

## Sprint 7 — Threat Graph + MITRE Coverage Grid

**Goal** Surface `/iag` graph as full-page Threat Graph + render the 34-signal MITRE coverage grid (data already exists in `services/security/signal_registry.py`).

**Files**
- Frontend
  - `ui/src/pages/ThreatGraph.jsx` (NEW, React Flow over `/iag/agents/{id}`).
  - `ui/src/components/security/MitreCoverageGrid.jsx` (NEW; pulls from `/iag/mitre-coverage` — add small read-only endpoint).
- Backend
  - `services/security/iag/router.py` — `GET /iag/mitre-coverage` — returns the 34-signal grid metadata.

---

## Sprint 8 — Blast Radius Dollar Formula

**Goal** Sum-over-(reachable system × tagged value) on every incident → dollar BlastRadiusCard.

**Files**
- Backend
  - `services/security/iag/router.py` — extend blast-radius response with `dollar_estimate`.
  - `services/identity/router.py` — `PATCH /workspace/system-values` (OWNER role) — value-tag config (system → dollar).
- Frontend
  - `WorkspaceSettings.jsx` — System Values tab.
  - `BlastRadiusCard.jsx` — render the dollar number.

---

## Sprint 9 — Stripe Billing Wiring

**Goal** Customer can upgrade plan in `Settings → Billing`. Stripe webhook drives tenant.tier patches via existing `PATCH /admin/tenants/{tenant_id}`.

**Files**
- Backend
  - `services/gateway/routers/stripe_webhook.py` — extend existing handler for `customer.subscription.{created,updated,deleted}` → `PATCH /admin/tenants/{id}` with new tier. (Webhook receiver scaffold already exists.)
  - `services/billing/router.py` — `POST /billing/checkout-session` (Stripe Checkout) + `POST /billing/portal-session` (Customer Portal).
- Frontend
  - `ui/src/pages/Billing.jsx` — show current plan + "Upgrade to Pro" button → redirect to Checkout.

---

## Sprint 10 — Production Hardening

**Goal** Close the prod-grade gaps: CSP, security headers, audit chain refresh cron, deploy-time secrets rotation.

**Files**
- `services/gateway/middleware.py` — add CSP `default-src 'self'; connect-src 'self' https://*.clerk.accounts.dev https://api.clerk.com; frame-ancestors 'none'; …`. Strict-Transport-Security max-age=31536000. Permissions-Policy. Referrer-Policy=no-referrer.
- `scripts/ops/rotate_clerk_keys.py` — generate new `whsec_` + push to SSM Parameter Store; ASG refresh.
- `scripts/ops/build_release_bundle.sh` — add `--exclude='./.env'` to stop shipping repo-root dev secrets in the tar.

---

## Execution policy (read every sprint)

1. **Plan**, **build**, **test**, **commit**, **bundle**, **upload**, **ASG refresh**, **probe** — in that order. Skipping any step closes the sprint as failed.
2. After ASG refresh, smoke probes go in this file under that sprint's "Evidence" line. Cite request_id + HTTP code, not vibes.
3. If a probe fails, the sprint stays open. No moving on.
4. Founder approval gate: before `git push`, before `aws s3 cp`, before `start-instance-refresh` — ask. Sprint 1 had implicit approval baked into the kickoff prompt; subsequent sprints get the prompt explicitly.
5. Memory: after each successful sprint, write a short memory file + index entry so future sessions don't repeat the "what's the alembic head" / "where's the bundle script" rediscovery cost.
