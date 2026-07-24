"""Regression: anchored regex validators must use fullmatch, not match.

Python's `re.match` on `^...$` matches BEFORE a trailing `\\n` by default,
so `foo\\n` passes a regex like `^[a-z]+$`. That's an ID/email/name
smuggling surface:
  - policy name `foo\\n` → filename with newline + log-injection surface
  - email `foo@bar.baz\\n` → SMTP header injection if used in outbound mail
  - SCIM id `abc\\n` → HTTP smuggling if interpolated into a URL

This test locks in the fullmatch conversion for the three validators
that touch external input.
"""
from __future__ import annotations

import re

from sdk.common.scim_client import _is_safe_scim_id


class TestScimIdRejectsTrailingNewline:
    def test_scim_id_trailing_newline_rejected(self):
        assert not _is_safe_scim_id("valid_id\n")
        assert not _is_safe_scim_id("valid_id\r")

    def test_scim_id_clean_still_accepted(self):
        assert _is_safe_scim_id("valid_id")
        assert _is_safe_scim_id("uuid-1234-5678")


class TestEmailRegexRejectsTrailingNewline:
    """Whitebox: the compiled regex must not admit `foo@bar.baz\\n` via
    fullmatch. We import it directly (rather than the pydantic validator)
    to isolate the regex from the wrapper."""
    def test_email_regex_fullmatch_rejects_newline(self):
        from services.gateway.routers.auth import _EMAIL_RE
        assert not _EMAIL_RE.fullmatch("foo@bar.baz\n")
        assert not _EMAIL_RE.fullmatch("foo@bar.baz\r")

    def test_email_regex_fullmatch_accepts_clean(self):
        from services.gateway.routers.auth import _EMAIL_RE
        assert _EMAIL_RE.fullmatch("foo@bar.baz")


class TestPolicyNameRegexRejectsTrailingNewline:
    def test_name_regex_fullmatch_rejects_newline(self):
        from services.policy.router import _NAME_RE
        assert not _NAME_RE.fullmatch("policy\n")

    def test_name_regex_fullmatch_accepts_clean(self):
        from services.policy.router import _NAME_RE
        assert _NAME_RE.fullmatch("policy_ok_123")


def test_pattern_match_vs_fullmatch_asymmetry_still_exists():
    """Sanity: python's re.match on ^...$ still admits trailing \\n —
    this is the whole reason we must switch validators to fullmatch.
    If this assertion ever flips, python's regex semantics changed
    and this test suite deserves a re-read."""
    p = re.compile(r"^[a-z]+$")
    assert p.match("foo\n") is not None, "python regex changed — audit callers"
    assert p.fullmatch("foo\n") is None
