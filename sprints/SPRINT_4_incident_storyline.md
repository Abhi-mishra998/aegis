# Sprint 4 — Incident Storyline Engine

**Status:** in_progress
**Closes debt:** TD-6 — flat findings, no MITRE chain, SOC analyst gets 5 alerts for 1 kill chain.
**Depends on:** Sprint 1 (Signal Registry — MITRE tags), Sprint 3 (Objectives — clean per-tactic surface).
**Blocks:** Sprint 5 (Blast Radius is computed per Incident), Sprint 6 (Auto-Remediation fires per Incident).

---

## Why this matters

Today every finding goes out the door as its own event. A real kill chain that touches 5 of Aegis's rules in 30 seconds shows up as 5 unrelated 403s. The SOC analyst has to mentally reconstruct: *"these all came from the same session, from the same agent, against the same target — this was one attack."*

EDR products don't do that. CrowdStrike Falcon shows a single "Detection" with a process tree and a MITRE-tactic timeline. Defender shows "Incident" with related alerts grouped. SentinelOne calls it "Storyline."

Sprint 4 is Aegis's storyline engine. One incident object per kill chain. The SOC sees `Incident INC-2026-…: TA0007 → TA0009 → TA0010, 3 participating agents, blocked at step 4 by SEC-EXFIL-001` — not 5 separate findings.

## Goal

Every finding at tier ≥ `escalate` is **automatically linked into an Incident** that captures:

- **MITRE tactic chain** — ordered by observation, deduped on consecutive repeats. e.g. `[TA0007, TA0009, TA0010]`.
- **Technique chain** — same ordering but technique-level. e.g. `[T1087, T1213, T1567.002]`.
- **Participating agents** — all agents that contributed a finding to this story.
- **Time window** — `start_ts`, `last_event_ts`, `end_ts` (set when incident closes).
- **Step list** — one row per finding with `(seq, ts, agent_id, signal_id, mitre, tier, policy_id, target, explanation)`.
- **Outcome** — `status` ∈ `{open, blocked, quarantined, resolved}`; `blocked_at_step` (which step finally denied); `blocking_policy_id`.
- **Title + narrative** — auto-generated; the one-line title is what shows in the SOC list view, the narrative is the per-step explanation.

The data lives in Redis (hot, TTL-decayed). DB persistence and UI integration are explicit Sprint 4 non-goals; once the engine is shipped, the autonomy service can poll Redis or subscribe to the stream for cold storage.

## Algorithm

Grouping decides which findings join the same story:

1. **Session-key first** — if the call carries `X-Session-ID`, the story key is `(tenant_id, session_id)`. This is the common case and keeps stories tight.
2. **Cross-agent override** — if the finding is `cross_agent_kill_chain` (Sprint-1 GAP-5), every agent referenced in `canonical.cross_agent_chain.agent_ids` is folded into the same story even if they used distinct sessions. The story key for the umbrella record is `(tenant_id, "xagent:" + first_agent_id_seen)`.
3. **Fallback agent-window** — no session and no xagent: the story key is `(tenant_id, agent_id)` with a 30-minute idle window. After 30 min of silence the next finding opens a new story.

MITRE chain rules:
- Sort steps by `ts` ascending.
- Map each step's `signal_id` → `mitre_tactic` and `mitre_technique` via the Sprint-1 registry.
- Dedup CONSECUTIVE identical entries only — repeated `T1213` reads stay in the technique chain only once until something else lands. (Three back-to-back bulk-PII reads → one entry, not three.)
- Result: ordered, deduped lists.

Status transitions:
- `open` — at least one finding recorded, none denied yet.
- `blocked` — most recent finding has tier `deny`. Sets `blocked_at_step` to the step's seq.
- `quarantined` — most recent finding has tier `quarantine`.
- `resolved` — operator-triggered close (out of scope for Sprint 4 — the API will accept it but only via an admin endpoint).

Title generation: `<TA0007>→<TA0009>→<TA0010>` with the human MITRE-tactic labels: "Discovery → Collection → Exfiltration". Truncated at 5 tactics with `…` if longer.

Narrative generation: per-step, one short sentence summarising the finding. Inputs are the finding's `policy_id`, `target` (table / host / file), and `explanation`. Sprint 4 ships a deterministic template; Sprint 6 can pipe to an LLM for richer prose if the user wants it.

## Success criteria

1. New module `services/security/incidents/storyline.py` — pure reconstruction logic. Input: list of `Step` records. Output: a `Storyline` dataclass with tactic chain, technique chain, steps, status, blocked_at_step, title, narrative.
2. New module `services/security/incidents/recorder.py` — Redis-backed writer. Exposes `async def record_step(redis, tenant_id, agent_id, session_id, finding_signals, mitre, tier, policy_id, target, explanation, cross_agent_chain) -> str` returning the `incident_id`. Idempotent on retry.
3. New module `services/security/incidents/store.py` — Redis read API: `async def get(redis, tenant_id, incident_id) -> Storyline | None`; `async def list_open(redis, tenant_id, since_ts) -> list[Storyline]`.
4. New router `services/gateway/routers/incidents.py` — `GET /incidents/{id}` returns the storyline JSON; `GET /incidents` lists open incidents for the tenant. Both require the existing tenant JWT.
5. Gateway middleware hook: after every deny / escalate / quarantine, call `recorder.record_step(...)` before returning the response. Failure to record must not affect the user response (fail-open on incident writes).
6. Unit tests:
   - `test_storyline_groups_by_session.py` — three findings in one session = one incident, three sessions = three incidents.
   - `test_storyline_cross_agent_folds_into_one.py` — `cross_agent_kill_chain` signal folds 4 agents into one story.
   - `test_storyline_mitre_chain_dedups_consecutive.py` — three back-to-back bulk_pii reads = one entry in the technique chain.
   - `test_storyline_status_transitions.py` — open → blocked when a deny finding lands; → quarantined when a quarantine finding lands.
   - `test_storyline_title_truncates_long_chains.py` — 7-tactic chain renders as `A → B → C → D → E → …`.
7. Live: after Sprint 4 deploys, an end-to-end probe that fires `SELECT info_schema → SELECT customers/ssn → tar+curl→transfer.sh` against a single agent produces ONE Incident with `mitre_chain=[TA0007, TA0009, TA0010]`, three steps, and status `blocked` at step 3.

## Non-goals

- **PostgreSQL persistence.** Redis-only for Sprint 4. The autonomy service's existing `aegis_incidents` table is untouched. Sprint 6 (Auto-Remediation) can wire the durable copy.
- **UI surface.** The API returns JSON. The Approval Inbox / Incidents page rendering is a separate UI task.
- **LLM-generated narrative.** Template-based deterministic narrative only.
- **Cross-tenant correlation.** Stories are tenant-scoped.

## Files

**Added:**
- `services/security/incidents/__init__.py`
- `services/security/incidents/storyline.py` — pure reconstruction.
- `services/security/incidents/recorder.py` — Redis-backed writer.
- `services/security/incidents/store.py` — Redis read API.
- `services/gateway/routers/incidents.py` — HTTP endpoints.
- `tests/security/test_incident_storyline.py` — unit tests for the algorithm.
- `tests/security/test_incident_recorder.py` — integration tests with a fake Redis.

**Modified:**
- `services/gateway/middleware.py` — hook after deny / escalate / quarantine to call `record_step`.
- `services/gateway/main.py` — register the new router.
- `infra/docker-compose.yml` — already mounts services/security into policy + decision (Sprint 1).

## Migration shim

None. New endpoints are additive (`/incidents`); existing callers untouched.

## Rollback

`git revert` removes the modules + the router + the middleware hook. Redis keys decay via TTL within 24 h. No DB writes to undo.

## Verification before sprint closes

- [x] All 81 prior tests pass unchanged.
- [x] 5 new incident-storyline tests pass.
- [x] Live: SSM-verified `from services.security.incidents import storyline, recorder, store` imports cleanly in `acp_gateway`.
- [x] Manual smoke: emit a 3-step kill chain → fetch `/incidents` → verify single incident with correct mitre_chain and blocked_at_step.
