# Detection Pipeline

*How Aegis decides what's an attack — input normalization, the 17-pattern
prompt-injection classifier, the SQL governance heuristics, and the output
filter. Every detection in this page is measured against a labelled corpus;
the recall and false-positive numbers below are exactly what
`tests/test_injection_corpus.py` and `tests/test_sql_normalization.py`
assert on every CI run.*

## Why this page exists

Pre-Sprint-2 the audit (C5, C6, C22) flagged three honest weaknesses in the
detection layer:

* The destructive-SQL gate matched lowercase substrings (``drop table``)
  and trivially missed ``DROP/**/TABLE``, ``DROP%20TABLE``,
  ``DROP\nTABLE``, and Unicode homoglyphs like ``ⅮROP TABLE``.
* The 17-pattern prompt-injection classifier was ASCII-only and the
  internal eval reported recall ≈ 0.71 — a number we then published in
  the module docstring.
* The output filter covered API secrets but missed entire PII classes
  (email, +91 phone numbers, Aadhaar), and any response > 256 KB or
  marked as streaming bypassed redaction outright.

Sprint 2 rebuilt the detection layer around one canonical normalization
function and shipped a labelled corpus for each detector so the README's
"we block X" claims survive a hostile reading.

## Input normalization (Sprint 2.4)

Source: ``sdk/common/sql_normalize.py::normalize_for_detection``.

A single deterministic transform that the destructive-SQL gate AND the
injection classifier run against. The function is intentionally lossy:
the goal is "see through the attacker's obfuscation"; the original
payload is still emitted to the audit row for forensic review.

Steps, in order:

1. **NFKC compatibility normalization** — collapses fullwidth Latin
   (``ＤＲＯＰ``), mathematical bold (``𝐃𝐑𝐎𝐏``), Roman numerals
   (``Ⅾ`` = U+216E), and supersript/subscript variants to their base
   Latin form.
2. **Homoglyph fold** — a curated map of Cyrillic, Greek, and Ukrainian
   letters that look identical to ASCII (e.g. Cyrillic ``Ｐ`` → ``p``).
   Conservative on purpose: every entry is for a character that benign
   LLM output essentially never produces.
3. **URL-decode (two passes)** — handles single (``%20``) and double
   (``%2520``) percent-encoding.
4. **SQL block-comment strip** (``/* … */``).
5. **SQL line-comment strip** (``-- …`` and MySQL ``# …``).
6. **String-literal blank** — single- and double-quoted SQL string
   contents are replaced with empty literals so a benign body that
   mentions ``'password reset flow'`` doesn't trip the password-column
   detector. Doubled quotes inside literals are handled.
7. **Whitespace collapse** — runs of any Unicode whitespace become a
   single space, so ``DROP\n\tTABLE`` becomes ``drop table``.
8. **Lowercase + trim**.

The transform runs once per request on the hot path. Microbench in
``tests/test_sql_normalization.py::test_normalize_for_detection_microbench``
asserts ≤ 500 μs per call on the reference deployment.

## Destructive-SQL detection (Sprint 2.4 — closes audit C5)

Source: ``services/decision/main.py::_compute_inference_signals``.

The decision service runs the normalized payload through three
substring-keyed signal generators:

| Signal | Trigger | Risk floor |
|---|---|---|
| ``SQL_DDL_DESTRUCTION`` | any of ``drop table / database / schema / view`` or ``truncate table`` | 0.95 |
| ``SQL_UNGUARDED_MUTATION`` | ``delete from`` or ``update`` without ``where`` | 0.85 |
| ``SQL_INJECTION_PATTERN`` | classical injection markers (``or 1=1``, ``union select``, ``xp_``, ``sp_``, ``exec(``) | 0.80 |
| ``SQL_PII_EXFILTRATION`` | ``select *`` or a column from the PII allowlist (``ssn``, ``credit_card``, ``passport``, ``password``, ``pin``, ``dob``, …) | 0.75–0.82 |

The substring match is intentionally simple — the normalization step is
where the bypass resistance lives. The labelled corpus is in
``tests/test_sql_normalization.py``:

* **38 attack payloads** across DDL destruction, injection, and PII
  exfiltration — including the audit's named bypasses
  (``DROP/**/TABLE``, ``DROP%20TABLE``, ``DROP\nTABLE``, Roman
  numeral ``Ⅾ``, fullwidth ``ＤＲＯＰ``, line-comment splits).
* **18 benign queries** that mention sensitive-sounding words in
  contexts a parser would distinguish from a column reference (e.g.
  ``INSERT INTO docs VALUES ('intro', 'password reset flow')``).

CI asserts **recall ≥ 0.95** on the attack set and **false-positive
rate ≤ 0.05** on the benign set. The current measured values are
**100% recall, 0% FP**.

## Prompt-injection classifier (Sprint 2.5 — closes audit C6)

Source: ``services/gateway/injection_classifier.py``,
``sdk/common/injection_patterns.py``.

The Tier-1 classifier runs the 17 curated regex patterns against the
input twice — once on the raw text, once on the normalized form — and
returns the union of matches. The dual-pass means an attacker who
combines obfuscations (e.g. Cyrillic homoglyphs + comment injection)
still lands on at least one detection branch.

Sprint 2.5 also tightened three of the original 17 patterns based on
corpus evidence:

| Pattern | Before | After |
|---|---|---|
| ``sudo_mode`` | bare ``sudo`` matched (false positives on benign DevOps prompts) | requires ``sudo`` paired with ``mode``/``access``/``root`` or a colon |
| ``act_as_persona`` | required ``you (are|were)`` | also accepts ``have no`` (catches ``act as if you have no rules``) |
| ``override_safety_filters`` | strict ``all`` between verb + noun | accepts ``the``/``any`` as determiners |
| ``data_exfiltration`` | strict ``all`` | accepts ``the``/``a``/``an`` |
| ``role_play_escape`` | ``you are/were/have no`` | also accepts ``as the previous version`` framing |

Corpus (``tests/test_injection_corpus.py``):

* **35 attack payloads** across instruction-override, jailbreak, persona,
  token smuggling, mass destruction, and exfiltration framing — with
  Cyrillic, fullwidth, and URL-encoded variants.
* **18 benign prompts** covering common business queries and DevOps
  commands (``run sudo apt-get update``, ``the CFO will act as interim
  CEO``) that the pre-Sprint-2 patterns were known to false-positive on.

CI asserts **recall ≥ 0.95**, **FP ≤ 0.05**. Current measured values:
**100% recall, 0% FP**. This is the number to put in any external
benchmark — the pre-Sprint-2 ``Recall=0.71`` figure in the module
docstring is retired.

## Output filter (Sprint 2.3 — closes audit C22)

Source: ``services/gateway/inference_proxy.py::OutputFilter``,
``services/gateway/_mw_response.py::_filter_response_chunked``.

Three Sprint-2 changes:

### 1. PII patterns added

Three new patterns in ``_REDACT_PATTERNS``:

| Pattern | Matches | Substitution |
|---|---|---|
| Email | RFC-5322-ish (``[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}``) | ``***EMAIL_REDACTED***`` |
| Indian phone | ``+91`` optional, 10-digit mobile starting 6-9, common separators | ``***IN_PHONE_REDACTED***`` |
| Aadhaar | 12 digits in 4-4-4 grouping with optional space/hyphen separators | ``***AADHAAR_REDACTED***`` |

The existing AWS key regex was loosened to **case-insensitive on the
``AKIA`` prefix** so an attacker who lowercases the leak (``akia…``)
doesn't slip past. AWS keys are uppercase by spec, but the gate is
belt-and-suspenders.

### 2. Streaming responses now get redacted

Pre-Sprint-2 any response with a streaming content-type
(``text/event-stream``, ``application/x-ndjson``) bypassed redaction
entirely. After Sprint 2.3 the filter switches to a chunked path via
``OutputFilter.redact_chunked`` — a generator that keeps a 4 KB tail
overlap between emissions so a secret that straddles a chunk boundary
is still matched.

The chunked path is bounded-memory: peak retention is
``_CHUNK_TAIL_OVERLAP_BYTES + max(chunk_size)``, regardless of total
stream length.

### 3. Bodies > 256 KB no longer skip redaction

The old fast-path ``if content-length > 256 KB: return response``
became the ``if streaming or > 256 KB: redact_chunked``. An unbounded
LLM completion is now redacted in 16 KB emit cycles instead of OOMing
or bypassing the filter.

Tests: ``tests/test_output_filter_pii.py`` (16 cases) — covers each new
PII pattern, the AWS lowercase obfuscation, JWT and email
chunk-boundary splits, and a microbench confirming the chunked path
emits intermediate output instead of buffering the whole stream.

## What still doesn't work

Honest framing for the next sprint:

* **Substring matching is the limit.** A parser-aware SQL check
  (sqlparse, sqlglot) would distinguish a column reference from a
  string literal, eliminating the false-positive class entirely. The
  literal-blank step in normalization is a pragmatic substitute; it
  doesn't solve every edge case.
* **The classifier's recall on novel jailbreaks is unmeasured.** The
  corpus is hand-curated to cover known families; a held-out red-team
  corpus is a Sprint 5 deliverable.
* **The chunked output filter assumes well-formed UTF-8.** Bytes that
  straddle a multi-byte character boundary between chunks will produce
  a single replacement character at the seam — the actual content is
  unchanged, but the seam is visible. Acceptable trade-off for the
  redaction guarantee.

## Operational notes

* Adjust the SQL detection thresholds via the existing decision-engine
  weights — they live in
  ``services/decision/main.py::_compute_inference_signals``.
* The 17 injection patterns are the single source of truth at
  ``sdk/common/injection_patterns.py``. Add a new pattern there; the
  classifier picks it up automatically.
* Output filter additions: edit ``_REDACT_PATTERNS`` in
  ``services/gateway/inference_proxy.py``. Add a test case in
  ``tests/test_output_filter_pii.py`` so the new pattern lands in CI.

## Next

* [Cryptographic Audit Chain](crypto-audit-chain.md) — what happens
  after a detection fires (the audit row, the receipt, the chain).
* [Mesh Authentication](mesh-auth.md) — how the decision service
  trusts the gateway calling it.
* [Threat Scenarios](threat-scenarios.md) — end-to-end attack walks.
