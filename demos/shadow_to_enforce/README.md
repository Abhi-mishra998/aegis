# 60‑Second Shadow → Enforce Demo

> The single narrative that converts a hesitant prospect into a signed
> pilot: deploy a candidate policy on Friday, review on Monday what it
> WOULD have blocked, promote with confidence. Zero risk to live traffic.

This demo follows the user through one full lifecycle of a shadow
policy: **draft → shadow → review → enforce → rollback**. It is
designed to be runnable end‑to‑end against the local docker‑compose
stack (or a live dev sandbox) in exactly 60 seconds of operator time.

The math under the hood is in
`tests/test_shadow_evaluator.py` and
`tests/test_shadow_hook.py` — read those to convince yourself the
guarantee is real before you demo it.

---

## Pre‑flight (one‑time)

```bash
# Verify the audit service is up and the gateway proxies /audit/shadow/*.
curl -sS https://dev.aegisagent.in/audit/shadow/policies \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -H "Authorization: Bearer ${ACP_TOKEN}" | jq '.data | length'
```

> If you don't have a token, follow `scripts/ops/run_e2e.sh` for the
> SSM credential pull — same path the e2e suite uses.

---

## 0:00 — Open the Shadow Mode page

Sidebar → **Shadow Mode**. The page splits into a left rail of
existing policies + a right pane that opens when one is selected.

> **What the buyer sees**: a screen explicitly labelled "Evaluate a
> candidate policy on 100% of live `/execute` traffic without changing
> what the pipeline actually decides." That sentence is the entire
> sale.

## 0:05 — Create a draft policy

Click **New draft**. Set:

- **Name**: `block-rm-rf`
- **rules_json**:

```json
[
  {
    "conditions": [
      { "field": "tool", "operator": "eq", "value": "tool.shell" },
      { "field": "payload_substring", "operator": "contains", "value": "rm -rf" }
    ],
    "action": "deny",
    "description": "Block destructive shell removal"
  }
]
```

Click **Save draft**. The policy lands with `mode=draft` — never
evaluated against traffic yet.

## 0:25 — Promote to shadow

In the policy detail pane, click **draft → shadow**.

Within one request, the gateway's in‑process cache picks up the new
mode (≤30s cache TTL is invalidated immediately by the promote API)
and starts evaluating this policy on every `/execute` call.

> **What the buyer sees**: the badge flips from `draft` to `shadow`.
> A version row appears in the history with `change_kind=promote`.

## 0:30 — Send some traffic

In another tab, run a few representative `/execute` calls (or replay
production traffic in your sandbox tenant). Mix:

- a real `rm -rf` payload (would be denied by the candidate)
- a benign `ls -la` (should NOT be denied — drift = potential FP)
- a benign `rm -rf /tmp/build_artifact` (genuinely safe `rm -rf`)

The live pipeline keeps making the same decisions it always did —
**nothing is blocked by the shadow policy**. The shadow eval runs in
the gateway's fire‑and‑forget background task.

## 0:50 — Review the would‑have‑denied report

Back on the Shadow Mode page, refresh. The detail pane shows:

| Metric | Value |
|---|---|
| Decisions seen        | 3 |
| Drift count           | 1 (the benign `rm -rf /tmp/build_artifact`) |
| FP rate               | 50% (1 of 2 real‑allowed) |
| Would‑have‑denied     | 2 (one true positive + one FP) |

> **The buyer pauses here**. The candidate would have blocked one
> piece of legitimate traffic. **They do NOT promote it.** That is the
> exact moment trust transfers — the system flagged a problem with
> the policy before they shipped it.

## 0:55 — Tighten the rule and replay

Edit the draft (or roll back to v1, edit, promote again). Add a
condition:

```json
{ "field": "payload_substring", "operator": "not_contains", "value": "/tmp" }
```

Send traffic again. Drift count drops to 0. FP rate is 0%.

## 1:00 — Promote to enforce

Click **shadow → enforce**. The version history records the promotion
with a timestamp. The policy is now operator‑approved for inclusion
in the live policy bundle (Sprint 7 turns this into a Rego deploy).

> **Or — if anything goes wrong**: every row in the version history
> has a **Rollback** button. One click restores `rules_json` + `mode`
> exactly as they were at that version.

---

## What the operator just proved to themselves

1. They can put a candidate policy in front of 100% of real traffic
   without risking any false‑positive blocking.
2. The drift report told them exactly which requests would have been
   wrongly blocked — they made the trade‑off with data, not vibes.
3. The promotion is reversible to any prior version with one click.
4. The whole flow is per‑tenant (and optionally per‑agent) — a buyer
   pilots on one agent before tenant rollout.

This is the narrative the audit explicitly called out as "the single
most important enterprise‑adoption feature in the whole plan." Sprint 6
shipped it.

---

## How to verify the no‑enforcement guarantee yourself

```bash
# Run the load-bearing test — shadow deny does NOT influence real_action.
python3 -m pytest tests/test_shadow_hook.py::test_shadow_deny_does_not_block_real_action -v
```

Expected output:

```
tests/test_shadow_hook.py::test_shadow_deny_does_not_block_real_action PASSED
```

Read the asserted invariants in the test source — the row stores
`real_action='allow'` even when the shadow policy returned `deny`,
and `would_have_blocked_benign(...)` returns `True`. That is the only
way the FP signal can reach the dashboard.
