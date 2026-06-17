# Aegis bug + cleanup ledger

> **Latest audit:** 2026-06-17 — Clerk integration senior-audit (PASS/FAIL with live HTTP evidence)
> **Prior audit:** 2026-06-17 — post-Sprint-23 dead-code sweep
> **Scope of scans:** `services/` + `sdk/` + `integrations/` + `ui/src/` + `scripts/ops/`
> **Excluded by founder direction:** `*.md` docs, build artifacts (`ui/dist`, `**/__pycache__`, `*.egg-info`, `build/`), `tests/`, alembic versions (append-only history).
> **Audit method (Clerk):** static + grep + live cross-tenant + RBAC + JWT-failure probes against prod-ha through the public ALB.

This file is the **single source of truth** for what's left to fix. Every item
below has a verification path and a parallel-resolvable scope. The previous
session shipped 24 commits + 14 sprints; the items here are what the audit found
NOT addressed by those.

---

## ✅ CL-3 Cross-tenant leak via X-Tenant-ID forge on skip-listed paths

**Severity:** HIGH. Confirmed cross-tenant data leak in production traffic.
**Discovered:** 2026-06-17 Clerk senior-audit live probe.
**Fixed:** commit `82c21a7`.

- **Root cause:** `services/gateway/_helpers.py:internal_headers()` forwarded the client's `X-Tenant-ID` verbatim. JWT-auth paths re-verified it against the JWT claim (`_mw_auth.py:313-315` → 403 "Tenant mismatch detected"). Skip-listed paths (`/v1/messages`, `/v1/chat/completions`, `/v1/approvals`, `/slack/`) trusted the api-key handler to pin `request.state.tenant_id` but `internal_headers` never read it as the canonical source.
- **Attack reproduced:** tenant B's `acp_emp_…` key on `/v1/messages` with `X-Tenant-ID: <tenant_A_uuid>` header:
  - Pack scan ran tenant B's prompt against tenant A's PCI Pack rules.
  - Slack-config fetch returned tenant A's webhook + signing secret.
  - Escalation card POSTed to tenant A's Slack channel with tenant B's prompt excerpt + employee email.
- **Fix:** `internal_headers()` now ALWAYS sources `X-Tenant-ID` + `X-Agent-ID` from `request.state.*` when set; falls back to client header only when state is unset (pre-auth / test utility paths). Same defence the helper has always used for `X-ACP-Role`.
- **Post-fix proof:** re-ran the same probe — both leaks closed. PCI escalation that fired pre-fix now returns 200 (no pack match against tenant B's empty list). `slack_notified` no longer set.
- **Regression check:** all 15 baseline Clerk probes still pass (`/tmp/clerk_audit_live.py` re-run).
- **Live evidence files:** `/tmp/clerk_audit_live.py` (15-probe baseline), `/tmp/clerk_xtenant_deep.py` (the breaking + fixed probe).

---

## ✅ Closed in the 2026-06-17 cleanup commit

These were the only meaningful findings the audit surfaced. All fixed in the
same turn that wrote this file — kept here for the ledger only.

### CL-1 Helper duplication between `messages.py` and `openai_messages.py`
- **Symptom:** 8 helpers (`_spend_key_day`, `_spend_key_month`, `_current_spend`, `_record_spend`, `_lookup_approval`, `_fetch_enabled_policy_packs`, `_fetch_tenant_slack_config`, `_post_slack_card`) had ~150 lines of duplicated body across the two router files.
- **Origin:** Sprint 22 inline-copied the helpers because of a perceived circular-import risk between the two routers. The comment at `openai_messages.py:58-62` admitted this trade-off.
- **Fix:** Extracted all 8 to a new leaf module `services/gateway/proxy_helpers.py` (no upstream import of either router → no circular reference). Picked the better-of-each-version: Redis pipeline (faster than 4 sequential awaits), `if cost <= 0: return` guard, 70-day month TTL, generic logger event names.
- **Line accounting:** `messages.py` 1953→1810 (−143), `openai_messages.py` 614→417 (−197), `proxy_helpers.py` +269. Net −71 lines AND duplication eliminated.
- **Verification:** post-refactor live smoke 9/9 pass — benign Anthropic 200, injection 403, base wire escalate 202, PCI pack escalate 202; OpenAI 503 (no upstream key), injection 403, base escalate 202, PCI pack escalate 202; approval status lookup `pending`.

### CL-2 Dead `_hash_key` stub in `messages.py:155`
- **Symptom:** `def _hash_key(raw_key)` returned `sha256(raw_key)`. Never called from anywhere in the codebase (the in-class `APIKeyRepository._hash_key` is a separate method on a different class).
- **Origin:** Probably an early Sprint-17 design before the `service_client.validate_api_key` HTTP path was added.
- **Fix:** Deleted. Also removed the now-unused `import hashlib`.
- **Verification:** `grep "_hash_key" services/gateway/ sdk/ integrations/` returns zero matches; gateway boots clean.

---

## ⏳ Open items (parallel-resolvable)

### BUG-1 `_PUBLIC_BASE_URL` duplicated in both routers
- **Files:** `services/gateway/routers/messages.py:62` + `services/gateway/routers/openai_messages.py:50`
- **Risk:** LOW. Same 1-line constant, no behavioural drift, but rule-of-three would put it in `proxy_helpers.py` next to the function that uses it.
- **Fix:** Move the constant to `proxy_helpers.PUBLIC_BASE_URL`; both routers read it from there; drop the local copies.
- **Why deferred:** Cosmetic — no shipping risk; the constant is identical in both files and never edited. Worth doing during the next sprint that touches either router.
- **Verification once done:** `grep _PUBLIC_BASE_URL services/gateway/` should show ONE definition (in `proxy_helpers.py`) and N call-sites.

### BUG-2 `rego_emitter.py` uses `print()` in production module
- **File:** `services/policy/rego_emitter.py:155, 157, 161`
- **Risk:** NONE — verified the prints are inside the `if __name__ == "__main__"` argparse `main()` handler. They're the CLI's user-facing output, where `print()` is the correct tool.
- **Status:** **DISMISSED — not a bug.** Documented here so a future audit doesn't re-flag it.

### BUG-3 `print()` references in `wizard.py`
- **File:** `services/registry/wizard.py:465, 497`
- **Risk:** NONE — these are inside STRING LITERAL templates that get returned to the customer as snippet code for the install instructions. They're not executable.
- **Status:** **DISMISSED — not a bug.**

### BUG-4 The `Replay` UI page renders raw `audit_rows` + `override_events` JSON without redaction
- **File:** `ui/src/pages/Replay.jsx` end of file (`<pre>...</pre>` block)
- **Risk:** MEDIUM — the prompt excerpt and metadata are already tenant-scoped via the gateway handler, so cross-tenant leakage isn't possible. But the raw JSON could include the full `prompt_excerpt` (capped at 240 chars upstream so usually fine) and the operator's note (could contain a vendor name etc.). For SOC2 evidence this is OK; for a customer screenshot embedded in a sales deck it's noisy.
- **Fix:** Either (a) hide the raw drawer behind an extra "Show raw" toggle for non-OWNER roles, or (b) redact long values in the rendered JSON.
- **Why deferred:** Not blocking any sale. The drawer is collapsed by default; opening it is an explicit operator action.

### BUG-5 `current.tar.gz` ASG bootstrap doesn't seed `UPSTREAM_OPENAI_KEY`
- **Status:** Same shape as the `UPSTREAM_ANTHROPIC_KEY` issue we fixed mid-session for the Anthropic proxy.
- **Risk:** LOW — `UPSTREAM_OPENAI_KEY` isn't set anywhere yet (the OpenAI proxy currently returns 503 on the forward path until an operator wires the key). When an operator does set it, they'll need to mirror the same launch-template overlay we did for Anthropic.
- **Fix:** Add `["${SSM_PREFIX}/openai/upstream-key"]="UPSTREAM_OPENAI_KEY"` to `infra/terraform/environments/prod-ha/user_data.sh` SSM_OVERLAY array. Bump launch-template version. (The repo's user_data.sh and the AWS launch-template can drift — same lesson learned with Anthropic.)
- **Why deferred:** No customer is using the OpenAI proxy in production yet; ship this when the first one is.

### BUG-6 Sprint 14 incident card shows "—" for `user_email` + `policy_id` + `mitre_technique`
- **Files:** `ui/src/pages/Incidents.jsx` row block; backend `services/api/models/incident.py`
- **Origin:** Sprint 14 surfaced the fields the UI wants but the `incidents` table only carries title / agent_id / tool / risk_score / decision / etc. The Sprint-19/23 audit-row metadata (`policy_id`, `mitre_technique`, `user_email` via `employee_email`) is on the `audit_logs` rows tied to the same `request_id`, not on the incident itself.
- **Risk:** LOW visual — for any audit row that has a `policy_id`, the Replay page already shows it. The Incident card is for incidents specifically (a different concept), and most incidents pre-Sprint-23 don't have policy_pack metadata.
- **Fix:** Denormalize on incident-row write: when the incident is created from an escalate audit row, copy `policy_id`, `mitre_technique`, `employee_email` into the incident's own columns (migration + writer update).
- **Why deferred:** Founder explicitly said "don't do cleanup; customers don't buy that." The card still works; fields just say "—" when the underlying data is unavailable. Fix when shipping the next Incidents-related sprint.

### BUG-7 No production-traffic load test of the new proxy paths
- **Files:** All of `services/gateway/routers/messages.py` + `openai_messages.py` + `proxy_helpers.py`.
- **Risk:** UNKNOWN — every sprint was live-tested with 1-5 prompts per scenario. We haven't load-tested under, say, 1000 concurrent employees against the proxies.
- **Fix:** Extend `tests/load/soak.py` with a Path-B scenario (mint 100 employee keys, fire `/v1/messages` at rate=100 r/s, verify p99 stays under 1500ms, verify no budget-counter drift).
- **Why deferred:** Pre-revenue. Add when there's an enterprise contract that demands a soak-test SLA.

### BUG-8 No CI gate on the helpers-import structure
- **Risk:** If a future sprint inlines a helper back into one of the routers (re-introducing duplication), nothing catches it.
- **Fix:** Tiny test in `tests/test_dry_proxy_helpers.py`: assert `messages.py` and `openai_messages.py` BOTH import from `proxy_helpers`; assert neither has a top-level `def _spend_key_day` / `def _current_spend` / etc.
- **Why deferred:** Nice-to-have hygiene; not a customer-facing issue.

---

## 🚫 Categories the audit found CLEAN

Documented here so the next auditor doesn't re-scan them:

- **`except Exception: pass` silent failures** — zero matches across `services/` and `sdk/`. Every catch logs via `structlog`.
- **`# TODO` / `# FIXME` / `# XXX` left in production code** — zero matches outside generated fixtures.
- **Hardcoded URLs / IPs / API tokens** — all URLs are either env-var-driven (`settings.AUDIT_SERVICE_URL`, `UPSTREAM_ANTHROPIC_KEY`) or legitimate constants (`https://api.anthropic.com/v1/messages`, `https://api.openai.com/v1/chat/completions`, `https://hooks.slack.com/` validation prefix). No bare tokens in source.
- **Orphaned React components** — App.jsx imports 57 page components; every one resolves to a `<Route>` (some redirect to consolidated tabs, but none are unimported orphans).
- **Unreferenced Python modules** — every `.py` under `services/` and `sdk/` is imported somewhere in the live import graph.
- **Unused API endpoints** — every `@router.get/post/...` in `services/gateway/routers/` has at least one consumer (UI or SDK or internal-secret callout).

---

## How to use this file

1. Pick any open item by ID (BUG-X).
2. The "Risk", "Fix", and "Why deferred" lines tell you whether it's worth a sprint and what changes.
3. When you close one, move it to the **✅ Closed** section at the top with a commit SHA + a one-line summary of what shipped.
4. Don't pad the open list. Real bugs only.
