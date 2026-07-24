"""Regression: the exfiltration credential-in-message-body detector
must not silently miss case-sensitive secret patterns when a caller
passes only `raw_norm` (lowercased upstream) and not
`raw_norm_original` (original case).

Case-sensitive patterns that silently missed under the old
`raw_orig = orig or lower or ""` fallback:
  * `AKIA[0-9A-Z]{16}\\b`  — AWS access key id
  * `\\beyJ...`             — JWT (starts with capital J)
  * `\\bghp_[A-Za-z0-9]{30,}` — GitHub PAT (lowercase prefix, mixed body)

Real callers set both fields (`canonical.py:915+925`), but test paths
and stale cached canonical results from before Sprint U13 can lack
`raw_norm_original`. Q28 fix runs every pattern against BOTH haystacks
when both are present, and against whichever is present when only one
is available — strict improvement in true-positive rate with no
false-positive change.
"""
from __future__ import annotations

from services.security.objectives.exfiltration import detect


def _aws_key_original_only(canonical: dict) -> dict:
    """Simulate a caller that only populated `raw_norm_original` — e.g.
    a legacy path that hasn't lowercased yet."""
    return canonical


class TestCaseSensitivePatternsMatch:
    def test_aws_akia_key_in_original_case_detected(self):
        """Sanity: with original case present, AKIA pattern fires."""
        c = {
            "raw_norm_original": "here is my key: AKIA" + "IOSFODNN7EXAMPLE thanks",
            "raw_norm":          "here is my key: akiaiosfodnn7example thanks",
        }
        assert "credential_in_message_body" in detect(c)

    def test_jwt_in_original_case_detected(self):
        """Sanity: JWT-shaped token in original case fires."""
        c = {
            "raw_norm_original": (
                "auth=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
                "eyJzdWIiOiJ4In0.sig1234567890"
            ),
            "raw_norm": (
                "auth=eyjhbgcioijiuzi1niisinr5cci6ikpxvcj9."
                "eyjzdwiioijjin0.sig1234567890"
            ),
        }
        assert "credential_in_message_body" in detect(c)


class TestBothSourcesSearched:
    def test_pattern_in_only_raw_norm_original_still_detected(self):
        """Pattern lives in original-case; raw_norm doesn't carry it.
        Both haystacks must be searched → detected."""
        c = {
            "raw_norm_original": "AKIA" + "IOSFODNN7EXAMPLE",
            "raw_norm":          "redacted",   # lowercased body without key
        }
        assert "credential_in_message_body" in detect(c)

    def test_pattern_in_only_raw_norm_still_detected(self):
        """Lowercase-safe pattern (e.g. `sk-` OpenAI prefix) present in
        raw_norm but not raw_norm_original — must still fire."""
        c = {
            "raw_norm_original": "clean",
            "raw_norm":          "sk-proj-" + "a" * 44,
        }
        assert "credential_in_message_body" in detect(c)


class TestFallbackWhenOnlyOneSourceProvided:
    def test_raw_norm_original_only_still_works(self):
        c = {"raw_norm_original": "AKIA" + "IOSFODNN7EXAMPLE"}
        assert "credential_in_message_body" in detect(c)

    def test_raw_norm_only_still_finds_lowercase_safe_patterns(self):
        """Legacy caller with only lowercased raw_norm — lowercase-safe
        patterns (openai `sk-`, anthropic `sk-ant-`, PEM header) still
        fire. Case-sensitive-only patterns (AKIA/JWT-capital-J) can't
        be recovered from lowercase — that's a known limit, not a
        regression this test can close."""
        c = {"raw_norm": "sk-proj-" + "a" * 44}
        assert "credential_in_message_body" in detect(c)

    def test_empty_input_no_finding(self):
        assert "credential_in_message_body" not in detect({})
        assert "credential_in_message_body" not in detect(
            {"raw_norm": "", "raw_norm_original": ""},
        )


class TestOldFallbackWouldHaveSilentlyMissed:
    """Canary: prove the OLD `raw_orig = orig or lower or ""` fallback
    was really broken. If someone reverts Q28, this canary still passes
    but the other tests fail loudly."""
    def test_aws_key_pattern_never_matches_lowercased_input(self):
        import re
        AKIA = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
        assert AKIA.search("AKIA" + "IOSFODNN7EXAMPLE") is not None
        # Lowercased → the case-sensitive pattern misses. This is why the
        # detector MUST search original-case when case-sensitive
        # patterns are in play.
        assert AKIA.search("akiaiosfodnn7example") is None
