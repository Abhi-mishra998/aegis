# ADR-NNN: <short imperative title>

* Status: Proposed | Accepted | Superseded by ADR-XYZ
* Date: YYYY-MM-DD
* Deciders: <names / roles>
* Tags: <area/area/…>

## Context

What problem are we solving? What forces are at play (compliance, latency,
team size, customer requests, prior incidents)? One or two short paragraphs.
Cite the evidence: tickets, incidents, customer requests, file paths.

## Decision

We will do **X**. Be specific. Name the libraries, the tables, the
algorithms. If the decision is "stay with the current approach", say so
explicitly — that's still a decision.

## Alternatives considered

For each one: a one-line description, then why we rejected it. Be honest;
"we didn't know how" is a valid rejection reason.

1. **Alt A.** Description. Rejected because …
2. **Alt B.** Description. Rejected because …

## Consequences

* **Positive** — what we gain.
* **Negative** — what we give up (a constraint we accept, a feature we can't
  build cheaply, an operational cost).
* **Reversibility** — how hard is it to undo if this turns out wrong?
  ("trivial" / "1-week migration" / "two-quarter rewrite" / "irreversible").

## Implementation references

* `path/file.py:line` — primary implementation
* `path/migrations/XXXX.py` — schema change
* `tests/path/test_XXX.py` — guard tests
* `docs/...` — operator-facing documentation

## Verification

How does an outside reader verify this decision is actually in force today?
A grep, a curl, a SQL query, a test command. One copy-pastable invocation.

```bash
# Example: prove the constraint is enforced
grep -rn "X-Tenant-ID" services/gateway/_helpers.py
```
