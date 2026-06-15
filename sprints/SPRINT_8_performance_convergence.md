# Sprint 8 — Performance + Rego/Python convergence

**Status:** in_progress
**Closes debt:** TD-8 (fast/slow path drift), TD-9 (multi-RTT hot path).
**Depends on:** Sprint 1 (Signal Registry), Sprint 3 (Security Objectives).
**Blocks:** —

---

## Why this matters

Today the request path makes **three sequential `ZRANGEBYSCORE` calls** in
`services/policy/risk_pipeline.py:cumulative_scores` — once for the
session window, once for the agent (60 min) window, once for the agent
(7 day) window. Each call is a full Redis RTT. On a warm-pool localhost
that's ~1 ms each; under load (queue + jitter) it's ~5 ms each — so
~15 ms of RTT alone on every /execute call. Every new signal we add
inflates this; the platform's p95 grows linearly.

Pipelining the three reads into ONE RTT is a 3× wall-clock win for that
phase and a one-line risk surface — the writes still happen on the
critical path, but reads can fan out cheaply.

Independently: the fast-path Python evaluator (`local_action_semantics.py`)
and the slow-path Rego (`action_semantics_deny.rego`) keep their pattern
lists in parallel. Sprint R0 surfaced this with a manual mirror — every
time a new pattern lands in Python a human has to remember to add it to
Rego. We've already lost that race twice in the audit log. The fix is to
extract the shared patterns into ONE Python catalog and have a generator
emit the Rego section from it; CI fails when the checked-in Rego drifts
from the generated output.

## Goal

1. `cumulative_scores` does **one** Redis RTT, not three.
2. The hot-loop pattern catalog (exfil hosts, offshore tokens, shell
   destruction patterns, SQL DDL destruction patterns) lives in ONE
   Python module both the Python evaluator and the Rego generator read
   from.
3. A Rego generator emits the deterministic section of
   `action_semantics_deny.rego` from the catalog; a drift test fails
   the build when the checked-in Rego no longer matches the generated
   output for that section.

## Algorithm

### Part A — Pipelined cumulative scoring

```python
async def cumulative_scores(redis, tenant_id, agent_id, session_id):
    now = int(time.time())
    pipe = redis.pipeline(transaction=False)
    pipe.zrangebyscore(_session_key(session_id), now - SESSION, "+inf")
    pipe.zrangebyscore(_agent_key(tenant_id, agent_id), now - AGENT, "+inf")
    pipe.zrangebyscore(_agent_long_key(tenant_id, agent_id), now - LONG, "+inf")
    session_m, agent_m, long_m = await pipe.execute()
    # Tally locally; identical decode logic as the sequential path.
```

Falls back to the sequential path on any exception so a Redis upgrade
that breaks pipeline semantics doesn't take detection down.

### Part B — Shared pattern catalog

`services/policy/pattern_catalog.py` exposes named tuples of patterns:

```python
EXFIL_HOSTS: tuple[str, ...] = (...)
OFFSHORE_TOKENS: tuple[str, ...] = (...)
SHELL_DESTRUCTION_PATTERNS: tuple[str, ...] = (...)
SQL_DDL_DESTRUCTION_PATTERNS: tuple[str, ...] = (...)
```

`canonical.py` and `local_action_semantics.py` import from here so they
stop duplicating constants. The threat-intel runtime layer (Sprint 7)
overlays additional values at request time; the catalog is the floor.

### Part C — Rego generator + drift test

`services/policy/rego_emitter.py` reads the catalog and emits Rego
fragments — one per pattern set, wrapped in a sentinel-delimited block
inside the existing `action_semantics_deny.rego`:

```rego
# --- BEGIN GENERATED:exfil_hosts ---
_exfil_hosts := { "transfer.sh", "pastebin.com", ... }
# --- END GENERATED:exfil_hosts ---
```

The generator is invocable as `python -m services.policy.rego_emitter
--check` (returns non-zero on drift) and `--write` (rewrites the file).
The pytest drift test calls `--check` so CI catches the drift.

The non-generated parts of the Rego (rule structure, helper functions)
stay hand-written — Sprint 8 ships the pattern-list convergence, not
a full Python→Rego transpiler.

## Success criteria

1. `services/policy/risk_pipeline.py` — `cumulative_scores` uses
   `redis.pipeline()` to fetch all three windows in one RTT.
   Fallback to sequential path on `pipeline()` failure.
2. `services/policy/pattern_catalog.py` — one Python module owning the
   shared lists. `EXFIL_HOSTS` + `OFFSHORE_TOKENS` initially; the
   destructive-shell + DDL patterns follow once the canonical
   integration is wired (Sprint 8.5 if scope creep).
3. `canonical.py` reads `EXFIL_HOSTS` + `OFFSHORE_TOKENS` from the
   catalog (the in-file constants become thin aliases for back-
   compatibility with any external import).
4. `services/policy/rego_emitter.py` — generator that emits Rego
   fragments for the catalog's lists. CLI: `--check` / `--write`.
5. `tests/policy/test_pattern_catalog.py` — basic shape (non-empty,
   sorted, unique).
6. `tests/policy/test_rego_drift.py` — calls the generator's `check()`,
   fails when the checked-in Rego section doesn't match the catalog.
7. `tests/policy/test_cumulative_scores_pipelining.py` — fake Redis
   with a pipeline recorder; assert exactly one `execute()` is called
   and the result matches the sequential path on the same input.
8. Live: a single warm `/execute` call shows the same final tier as
   before (no regression). p95 drop is not measured live in Sprint 8 —
   the rigorous benchmark lands as part of the Sprint 9 prod-readiness
   gate.

## Non-goals

- **Full Python→Rego transpiler.** Sprint 8 generates pattern-list
  fragments only; rule logic stays hand-written.
- **Behavior service pipelining.** Same RTT optimization could land
  on behavior-firewall reads, but they're already cached with a
  60-second TTL — diminishing returns.
- **A bench harness comparing pipelined vs. sequential live.** Sprint 9
  owns the formal numbers; Sprint 8 ships the change behind a fall-back
  with unit-test coverage proving correctness.
- **Redis client upgrade.** Pipelining works with the redis-py version
  already in `pyproject.toml`.

## Files

**Added:**
- `services/policy/pattern_catalog.py`
- `services/policy/rego_emitter.py`
- `tests/policy/test_pattern_catalog.py`
- `tests/policy/test_rego_drift.py`
- `tests/policy/test_cumulative_scores_pipelining.py`

**Touched:**
- `services/policy/risk_pipeline.py` — pipeline the 3 zrangebyscore.
- `services/policy/canonical.py` — re-export catalog constants.
- `services/policy/policies/action_semantics_deny.rego` — wrap the
  exfil + offshore lists in `# --- BEGIN GENERATED ---` /
  `# --- END GENERATED ---` markers so the emitter can rewrite them
  in place.

## Rollout + rollback

- Deploy + restart `acp_gateway`.
- Pipelining falls back to sequential on any exception; rollback is a
  single revert of `risk_pipeline.py`.
- Rego changes are pattern-list-level only; the existing rule logic
  is unchanged.
