# Aegis Detection Benchmark

> A living, honest measurement of Aegis's detection posture against the OWASP
> LLM Top‑10. Built in Sprint 5 alongside the in‑product Evaluation dashboard.

The benchmark exists for one reason: you should not have to take our word for
how well Aegis detects attacks. Every claim on this page is reproducible from
the corpus in `tests/eval/corpus/` against the running pipeline. If the
numbers move tomorrow, the dashboard moves with them. If the numbers
embarrass us, we publish anyway — that is the contract.

## 1. What we measure

The Evaluation Suite scores three things on every run:

- **Detection rate** — recall on labelled attack cases. *"Of the 480 attack
  payloads we sent, how many did the pipeline deny, throttle, or escalate?"*
- **False‑positive rate** — *"Of the 80 labelled benign payloads we sent,
  how many did the pipeline wrongly block?"* Lower is better; this is the
  number a buyer cares about more than detection.
- **Per‑rule efficacy** — for every rule that fired (policy rule id,
  behavior heuristic, injection pattern id), what fraction of its firings
  were correct catches vs false positives. Surfaces "the SQL‑injection
  rule degraded 12 points after the change you shipped Friday" before it
  reaches production.

Each evaluator returns the failed case IDs so any score on this page can be
clicked through to the exact payload that broke. There is no aggregate
without the receipts.

## 2. The corpus

| Slice          | Cases | Source |
|----------------|------:|--------|
| **Total**      | 560   | `tests/eval/corpus/corpus.jsonl` |
| Attack         | 480   | base attacks × mutations |
| Benign         | 80    | hand‑written legitimate tool calls |

Per OWASP LLM Top‑10 category:

| Category | Cases | Targets |
|----------|------:|---------|
| LLM01 Prompt Injection                | 160 | `InjectionDetector` |
| LLM02 Insecure Output Handling        | 50  | Output filter (PII / secrets) |
| LLM04 Model DoS                       | 40  | Behavior firewall (token / loop) |
| LLM06 Sensitive Information Disclosure| 50  | Path traversal + policy |
| LLM07 Insecure Plugin Design          | 120 | SQL injection + command injection |
| LLM08 Excessive Agency                | 40  | Autonomy contracts |
| LLM10 Model Theft                     | 20  | Egress policy |
| Benign                                | 80  | n/a |

Every base attack is hand‑written; mutations are deterministic, applied by
the generator (`tests/eval/corpus/generate.py`). Mutations cover the seven
obfuscation classes attackers actually use in the wild: case alternation,
whitespace + zero‑width joiners, SQL/Rego comment splitting, URL encoding,
base64 wrapping, Cyrillic/Greek homoglyphs, and multilingual translation.

The `benign` slice is the FP guardrail. We tighten detectors aggressively in
this codebase; the benign cases are what catches us when a tightened rule
starts blocking the legitimate `kubectl get pods -n staging` it should
allow.

## 3. Methodology

The runner (`services/audit/evaluation_runner.py`) replays the corpus
through the **real** gateway `/execute` endpoint — never a mock. The same
auth path, the same OPA evaluation, the same behavior firewall.

For each case it records:

- `actual_outcome` — what the pipeline returned (allow / deny / throttle /
  escalate / error)
- `findings` — the canonical findings array from the decision response
  (subset of `services/decision/findings.py::CANONICAL_FINDINGS`, 14 strings)
- `rule_attribution_json` — which rule fired, harvested from the decision
  metadata
- `latency_ms` — wall‑clock time for the single `/execute` call

A case is **passed** when:

- **attack**: the pipeline returned `deny` (or `throttle` / `escalate`)
- **benign**: the pipeline returned `allow`

Every other outcome counts as a miss.

Tenant scoping comes from the JWT — the runner mints a token via
`/auth/token` using the credentials in
`/aegis-playwright/E2E_*` SSM parameters, exactly the same path the e2e
suite uses. There is no internal‑secret bypass for evaluation traffic.

## 4. Run it yourself

```bash
# 1. Generate the corpus (only needed when base attacks change)
python3 -m tests.eval.corpus.generate

# 2. Seed it into the eval tables for a tenant
python3 -m tests.eval.corpus.seed \
    --tenant-id 00000000-0000-0000-0000-000000000001 \
    --dataset-name owasp_corpus_v1

# 3. Open the in‑product dashboard
#    Sidebar → Evaluation → "Run nightly corpus"
#    (or POST /audit/evaluation/jobs with the dataset id from step 2)

# 4. Watch the per‑rule trend
#    GET /audit/evaluation/efficacy/overview
#    GET /audit/evaluation/efficacy/trend
```

The runner is gated behind `AEGIS_EVAL_USER` + `AEGIS_EVAL_PASSWORD` env
vars — without them, the audit service boots fine but logs
`eval_runner_disabled` and never claims a job. This is intentional: we
will not silently spam `/execute` against a dev box that's missing
credentials.

## 5. Publishing the benchmark

The corpus itself is **public**. We make no claim of secrecy; an attacker
who reads it learns nothing they couldn't write in an afternoon. We gain
more from transparency than we lose from disclosure:

- buyers can audit the corpus before signing
- security researchers can submit additions
- regressions are visible to the world the same day they're visible to us

Export bundle:

```bash
python3 -m tests.eval.corpus.export --out=/tmp/aegis-benchmark.tar.gz
```

(The export script lives in `tests/eval/corpus/export.py` — produces a
deterministic tarball with `corpus.jsonl`, this `benchmark.md`, and the
`LICENSE` line that the GitHub release uses.)

## 6. The current number

The "current number" is intentionally not a static line in this doc. It is
whatever the dashboard says at
`/evaluation` for the tenant you're looking at. If you want a point‑in‑time
snapshot for an external report, query:

```
GET /audit/evaluation/efficacy/overview
```

and quote the `detection_rate`, `fp_rate`, `cases_evaluated`, and
`last_run_at` fields verbatim. The numbers must agree with the dashboard
to four decimal places — both read from the same `eval_job_results` rows.

## 7. What this benchmark does not cover

It is honest to call out the boundaries:

- **Multi‑turn social‑engineering chains** — the corpus is single‑shot.
  Real attackers stage attacks across turns; we measure that surface via
  the Flight Recorder and Decision Explorer, not this benchmark.
- **Bring‑your‑own tools** — the corpus exercises the tools listed in §2,
  not every tool you might attach. Detection on a custom `tool.your_custom`
  is only as strong as the policy and behavior heuristics you write for it.
- **Adaptive attackers** — once an attacker reads the corpus, they will
  iterate. The corpus is a floor on detection capability, not a ceiling.

Sprint 6 closes the multi‑turn and adaptive gaps via shadow‑mode online
evaluation; Sprint 7 turns the policy iteration loop into a single screen.
This document gets a §8 link when those land.
