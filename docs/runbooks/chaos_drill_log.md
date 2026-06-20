# Chaos drill log

Sprint EI-7 (2026-06-20). Append-only log of every chaos drill outcome.
Pairs with `docs/runbooks/dr_drill_log.md` (database restore drills) and
the nightly soak history under `reports/soak/`.

## How a drill is scored

A drill exercises the parametrized chaos suite at
`tests/chaos/test_resilience_live.py`. Each case kills (or saturates)
one stack component during a 30-second 10 req/s load against staging,
and asserts:

- `p95 latency < 5 s` during the kill window (or `< 8 s` for db-pool burst)
- `fail rate < 25 %` (or `crash rate < 5 %` for db-pool burst)
- Target container self-heals to healthy within 60 s
- No request hangs past 30 s

A drill **PASSES** only if every case passes.

## Scenarios in the suite

| Case | What it kills / does | Why it matters |
|---|---|---|
| `test_kill_during_load[acp_opa]` | docker kill the OPA policy daemon mid-load | Policy decisions must fail-closed (deny) when OPA is down, not silently allow |
| `test_kill_during_load[acp_policy]` | kill the policy service | Gateway must back-pressure cleanly without 5xx storm |
| `test_kill_during_load[acp_decision]` | kill the decision orchestrator | Verifies fan-out timeout handling + partial-result resilience |
| `test_kill_during_load[acp_redis]` | kill Redis | Exercises the Redis-fallback paths (SSE best-effort, in-memory session intelligence, behavior firewall consult) |
| `test_db_pool_exhaustion_under_burst` | 200 concurrent /execute calls in 5 s | Validates 429/503 back-pressure over uncaught 500 |

## Drill outcomes (newest first)

| Date (UTC) | Trigger | Result | Notes |
|---|---|---|---|
| `<no drills run yet>` | | | First nightly_chaos workflow run will land the first row here. Until that row is populated, the chaos test counts as code-verified but operationally unproven. |

## How to add a row

The nightly_chaos GH-Actions workflow appends here after a successful
SSM-driven run. For manual runs (operator, on-EC2), use the template:

```text
| 2026-MM-DD | nightly | PASS | 5/5 cases passed; longest p95 4.8 s during acp_opa kill |
| 2026-MM-DD | manual  | FAIL | acp_policy did NOT self-heal in 60 s — fixed by bumping `restart: always` policy. Issue #N. |
```

## If a drill fails

1. Page on-call. Open SEV-2 incident.
2. Halt the next nightly deploy until the failing case has a fix that
   would have made the test pass.
3. Repro locally on a docker-compose stack: `pytest -m chaos -v --tb=long
   tests/chaos/test_resilience_live.py::test_kill_during_load[<case>]`.
4. Root-cause + ship the fix; re-run the failing case via
   `scripts/chaos/run_via_ssm.sh` on staging; only then close the
   incident.
5. Append the failure + fix to the row above so the next reviewer sees
   the precedent.

## Why we run chaos nightly, not pre-release

Pre-release runs catch regressions in *known* failure modes. Nightly runs
catch slow drift — e.g., a dependency upgrade widens the Redis client's
default timeout, masking what used to be a 5-second p95 SLO breach. Only
nightly cadence surfaces that class of regression before a customer hits
it.
