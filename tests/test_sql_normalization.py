"""
Sprint 2.4 — destructive-action defense corpus tests (closes audit C5).

Two test surfaces:

1. **`normalize_for_detection`** is the canonical, lossy transform every
   destructive-action check now runs against. We pin its behavior on a
   small set of known-bypass payloads from the audit (DROP/**/TABLE,
   DROP%20TABLE, DROP\\nTABLE, Cyrillic homoglyphs, Roman-numeral D, etc.)
   so a future refactor can't quietly weaken the gate.

2. **Detection-rate + false-positive-rate corpus.** A labelled set of
   38 attack variants and 18 benign queries; the test computes the
   ratios and asserts thresholds that match the README's new measurable
   claim: ≥0.95 recall on the OWASP-derived attack set, ≤0.05 FP rate
   on the benign-control set.

The corpus is intentionally enumerated in the test source rather than
shipped as a separate file — it doubles as living documentation of
exactly what the gate catches.
"""
from __future__ import annotations

import re

from sdk.common.sql_normalize import normalize_for_detection

# ---------------------------------------------------------------------------
# The audit's exact bypass examples — must each collapse to a form that
# substring-matches on "drop table" / "drop database" / etc.
# ---------------------------------------------------------------------------

_AUDIT_BYPASS_EXAMPLES = [
    # Original audit citation: substring check sees `drop table`, naive
    # variant escapes.
    ("DROP TABLE customers",        "drop table"),
    ("drop/**/table customers",     "drop table"),
    ("DROP%20TABLE customers",      "drop table"),
    ("DROP%2520TABLE customers",    "drop table"),       # double-encoded
    ("DROP\nTABLE customers",       "drop table"),
    ("DROP\t\tTABLE customers",     "drop table"),
    ("DrOp TaBlE customers",        "drop table"),
    ("drop -- harmless\ntable customers",
                                    "drop table customers"),
    ("DROP # mysql comment\nTABLE customers",
                                    "drop table customers"),
    ("ⅮROP TABLE customers",        "drop table"),       # U+216E Roman D
    ("ＤＲＯＰ ＴＡＢＬＥ customers",  "drop table"),       # full-width
    ("𝐃𝐑𝐎𝐏 TABLE customers",        "drop table"),       # mathematical bold
    ("dROp     /* sneaky */    tABle    customers",
                                    "drop table customers"),
]


def test_audit_named_bypasses_collapse_to_canonical():
    for payload, expected_substring in _AUDIT_BYPASS_EXAMPLES:
        got = normalize_for_detection(payload)
        assert expected_substring in got, (
            f"normalize_for_detection({payload!r}) = {got!r} "
            f"did not contain expected {expected_substring!r}"
        )


def test_empty_and_none_safe():
    assert normalize_for_detection("") == ""
    assert normalize_for_detection(None) == ""    # type: ignore[arg-type]
    assert normalize_for_detection(123) == ""     # type: ignore[arg-type]


def test_legitimate_queries_pass_through_recognizable():
    """Normalization must not mangle a SELECT into looking like a DROP."""
    payload = "SELECT id, email FROM users WHERE tenant_id = $1"
    out = normalize_for_detection(payload)
    assert "select id, email from users where tenant_id" in out
    assert "drop" not in out


def test_normalization_idempotent():
    """Applying the transform twice equals applying it once — pins the
    canonical form so a future caller can normalize-then-cache safely."""
    raw = "DrOp /* x */ TABLE  /* y */  foo"
    once = normalize_for_detection(raw)
    twice = normalize_for_detection(once)
    assert once == twice


# ---------------------------------------------------------------------------
# Detection-rate / false-positive corpus
# ---------------------------------------------------------------------------

# Mirror of services/decision/main.py::_DDL_HARD + _INJECT + _PII_COLS so
# the corpus measures the *real* decision-service detector, not a synthetic
# subset.
_DETECT_SUBSTRINGS = [
    # _DDL_HARD
    "drop table", "drop database", "drop schema", "drop view",
    "truncate table", "truncate ",
    # _INJECT
    "where 1=1", "where 1 = 1", "or 1=1", "or '1'='1'",
    "union select", "union all select", "; drop", "xp_", "sp_", "exec(",
    # _PII_COLS
    "ssn", "credit_card", "creditcard", "social_security",
    "passport", "salary", "password", "pin", "dob",
    "date_of_birth", "account_number",
]


def _is_detected(payload: str) -> bool:
    norm = normalize_for_detection(payload)
    return any(p in norm for p in _DETECT_SUBSTRINGS)


# Each entry is (payload, category). Categories are documented for the
# operator-facing breakdown in the test output.
_ATTACK_CORPUS = [
    # ── DDL destruction (12)
    ("DROP TABLE users",                                       "ddl_destruction"),
    ("drop database production",                               "ddl_destruction"),
    ("DROP SCHEMA tenant_a CASCADE",                           "ddl_destruction"),
    ("drop view audit_summary",                                "ddl_destruction"),
    ("TRUNCATE TABLE customers",                               "ddl_destruction"),
    ("truncate orders",                                        "ddl_destruction"),
    # Audit-named bypasses
    ("DROP/**/TABLE customers",                                "ddl_destruction"),
    ("DROP%20TABLE customers",                                 "ddl_destruction"),
    ("DROP\nTABLE customers",                                  "ddl_destruction"),
    ("ⅮROP TABLE customers",                                   "ddl_destruction"),
    ("ＤＲＯＰ ＴＡＢＬＥ customers",                            "ddl_destruction"),
    ("drop -- sneaky\ntable customers",                        "ddl_destruction"),

    # ── SQL injection variants (14)
    ("SELECT * FROM users WHERE id = 1 OR 1=1",                "injection"),
    ("SELECT * FROM users UNION SELECT password FROM admin",   "injection"),
    ("SELECT * FROM users UNION ALL SELECT NULL, password",    "injection"),
    ("'; DROP TABLE users; --",                                "injection"),
    ("xp_cmdshell('whoami')",                                  "injection"),
    ("sp_executesql @cmd",                                     "injection"),
    ("EXEC(@payload)",                                         "injection"),
    ("UNION%20SELECT * FROM secrets",                          "injection"),
    ("union/**/select * from secrets",                         "injection"),
    ("UNIon SeleCT * from passwords",                          "injection"),
    ("union\nselect * from creds",                             "injection"),
    ("Ｕnion Select * From creds",                              "injection"),
    ("'OR\t1=1\t--",                                           "injection"),
    ("' OR '1'='1",                                            "injection"),

    # ── PII bulk exfiltration (12)
    ("SELECT ssn FROM users",                                  "pii_exfil"),
    ("select credit_card, name from customers",                "pii_exfil"),
    ("SELECT passport FROM applicants",                        "pii_exfil"),
    ("SELECT password FROM users LIMIT 10",                    "pii_exfil"),
    ("select pin, dob from accounts",                          "pii_exfil"),
    ("SELECT date_of_birth FROM patients",                     "pii_exfil"),
    ("SELECT account_number FROM bank",                        "pii_exfil"),
    ("SELECT social_security FROM employees",                  "pii_exfil"),
    ("SELECT%20ssn%20FROM%20users",                            "pii_exfil"),
    ("select/**/ssn/**/from/**/users",                         "pii_exfil"),
    ("SELECT SSN FROM USERS",                                  "pii_exfil"),
    ("ＳＥＬＥＣＴ ssn ＦＲＯＭ users",                          "pii_exfil"),
]

# Benign queries — must NOT trip the detector. Bounds the false-positive rate.
_BENIGN_CORPUS = [
    "SELECT id FROM users WHERE tenant_id = $1",
    "SELECT name, email FROM contacts LIMIT 100",
    "INSERT INTO audit_log (id, action) VALUES ($1, $2)",
    "UPDATE settings SET value = $1 WHERE key = $2 AND tenant_id = $3",
    "DELETE FROM session WHERE expires_at < NOW() AND tenant_id = $1",
    "SELECT COUNT(*) FROM orders WHERE created_at > $1",
    "WITH recent AS (SELECT id FROM events WHERE ts > $1) SELECT * FROM recent",
    "SELECT * FROM agents WHERE tenant_id = $1 AND status = 'active'",
    "SELECT id, name FROM tools WHERE tenant_id = $1",
    "SELECT receipt FROM audit_logs WHERE id = $1 AND tenant_id = $2",
    # A query that mentions 'password' as a literal value, not a column.
    "INSERT INTO docs (title, body) VALUES ('intro', 'password reset flow')",
    "SELECT body FROM docs WHERE title = 'pin diagram'",
    "SELECT created_at FROM events WHERE drop_count < 5",
    # A query that mentions a sensitive-sounding column in a non-SELECT.
    "ALTER TABLE users ADD COLUMN nickname TEXT",
    "CREATE INDEX idx_email ON users(email)",
    "SELECT total FROM (SELECT SUM(qty) AS total FROM orders) AS t",
    "BEGIN; INSERT INTO x VALUES (1); COMMIT;",
    "EXPLAIN ANALYZE SELECT id FROM big_table WHERE col = $1",
]


def test_detection_rate_meets_target():
    """Sprint 2 publishes one measured number for the destructive-action
    defense's recall on a labelled attack corpus. The test asserts the
    threshold so the README claim stays honest after future edits."""
    by_category = {}
    detected = 0
    for payload, category in _ATTACK_CORPUS:
        ok = _is_detected(payload)
        if ok:
            detected += 1
        by_category.setdefault(category, [0, 0])
        by_category[category][0 if ok else 1] += 1

    recall = detected / len(_ATTACK_CORPUS)
    # Per-category breakdown — surfaced via assertion message so the
    # detection-rate trend over time is inspectable when the test runs.
    breakdown = ", ".join(
        f"{cat}: {hits}/{hits+miss}" for cat, (hits, miss) in sorted(by_category.items())
    )
    assert recall >= 0.95, (
        f"detection recall {recall:.2%} below 0.95 target — {breakdown}"
    )


def test_false_positive_rate_below_target():
    fired = []
    for payload in _BENIGN_CORPUS:
        if _is_detected(payload):
            fired.append(payload)
    fp_rate = len(fired) / len(_BENIGN_CORPUS)
    assert fp_rate <= 0.05, (
        f"false-positive rate {fp_rate:.2%} above 0.05 target. "
        f"Triggered on: {fired}"
    )


def test_normalize_for_detection_microbench():
    """Sanity-check the cost of the hot-path call so it doesn't silently
    balloon. Not a strict perf gate; emits the timing for visibility."""
    import time
    payload = "SELECT id, email FROM users WHERE tenant_id = $1 " * 64
    t0 = time.perf_counter()
    for _ in range(1000):
        normalize_for_detection(payload)
    elapsed_us = (time.perf_counter() - t0) * 1e6 / 1000.0
    assert elapsed_us < 500, f"normalize too slow ({elapsed_us:.1f} μs per call)"
