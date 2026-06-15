"""
Sprint 2.3 — output-filter coverage tests (closes audit C22).

Before this sprint the filter covered API secrets (JWTs, AWS uppercase keys,
PEM, hex blobs) but missed PII (emails, +91 phones, Aadhaar) and bypassed
both streaming responses and bodies > 256 KB. This test set pins:

  * Each new PII pattern matches realistic shapes and does NOT match an
    obvious negative-control near-miss.
  * The AWS-key regex now catches the lowercased obfuscation.
  * The chunked redactor matches secrets even when they straddle a chunk
    boundary — proving the streaming path can't be bypassed by an
    attacker who fragments the secret across emissions.
"""
from __future__ import annotations

import re

from services.gateway.inference_proxy import OutputFilter, inference_proxy


# ---------------------------------------------------------------------------
# Single-pattern coverage
# ---------------------------------------------------------------------------


def _redact(s: str) -> str:
    return OutputFilter.redact(s)


def test_email_address_is_redacted():
    out = _redact('User email is alice.bob+test@example.co.uk for billing.')
    assert "alice.bob+test@example.co.uk" not in out
    assert "***EMAIL_REDACTED***" in out


def test_email_redaction_is_case_insensitive_on_domain():
    out = _redact("ADMIN@AEGISAGENT.IN sent the alert.")
    assert "ADMIN@AEGISAGENT.IN" not in out


def test_email_does_not_swallow_atless_text():
    out = _redact("The hash is abc#xyz; not an email.")
    assert "abc#xyz" in out
    assert "***EMAIL_REDACTED***" not in out


def test_indian_phone_with_country_code_is_redacted():
    for shape in (
        "+91 9876543210",
        "+91-9876543210",
        "+919876543210",
        "+91.987.654.3210",
    ):
        out = _redact(f"Reach me at {shape} after hours.")
        assert shape not in out, f"failed for {shape!r}"
        assert "***IN_PHONE_REDACTED***" in out


def test_indian_phone_without_country_code_is_redacted():
    # Standalone 10-digit number starting 6-9 (Indian mobile prefix).
    out = _redact("My number is 9876543210, call me.")
    assert "9876543210" not in out


def test_phone_skips_obvious_non_matches():
    """A 10-digit number that does NOT start 6-9 is not an Indian mobile —
    bounds the false-positive rate against generic numeric IDs."""
    out = _redact("Order ID 1234567890 dispatched.")
    assert "1234567890" in out


def test_aadhaar_format_is_redacted():
    for shape in ("1234 5678 9012", "1234-5678-9012", "123456789012"):
        out = _redact(f"Aadhaar {shape} on file.")
        assert shape not in out, f"failed for {shape!r}"
        assert "***AADHAAR_REDACTED***" in out


def test_aws_key_lowercase_obfuscation_is_caught():
    """The audit (C22) flagged that the AWS key pattern was case-sensitive
    on the AKIA prefix — an attacker echoing `akia...` slipped past."""
    out = _redact("Bad leak: akiaiosfodnn7example.")
    assert "akiaiosfodnn7example" not in out.lower()
    assert "AWS_KEY_REDACTED" in out


def test_aws_key_uppercase_still_caught():
    out = _redact("Bad leak: AKIAIOSFODNN7EXAMPLE.")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "AWS_KEY_REDACTED" in out


def test_pem_private_key_is_redacted():
    """Pre-existing behavior — pinned here so the new PII patterns don't
    accidentally regress the secret patterns."""
    pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQ==\n"
        "-----END PRIVATE KEY-----"
    )
    out = _redact(f"Here is the key: {pem}")
    assert "PRIVATE KEY" not in out
    assert "***PEM_KEY_REDACTED***" in out


# ---------------------------------------------------------------------------
# Chunked / streaming redaction — boundary safety
# ---------------------------------------------------------------------------


def test_chunked_redaction_handles_split_email():
    """Email straddles a chunk boundary. The chunked redactor's tail
    overlap must match the split secret across emissions."""
    chunks = [b"prefix alice", b"@example.com suffix"]
    out = b"".join(OutputFilter.redact_chunked(chunks))
    assert b"alice@example.com" not in out
    assert b"***EMAIL_REDACTED***" in out


def test_chunked_redaction_handles_jwt_split_across_chunks():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.signature"
    # Split the JWT at a sub-segment boundary.
    chunks = [
        f"prefix {jwt[:25]}".encode(),
        f"{jwt[25:]} suffix".encode(),
    ]
    out = b"".join(OutputFilter.redact_chunked(chunks))
    assert jwt.encode() not in out
    assert b"***JWT_REDACTED***" in out


def test_chunked_redaction_passes_clean_data_through_unchanged():
    chunks = [b"The quick brown fox ", b"jumps over the lazy dog."]
    out = b"".join(OutputFilter.redact_chunked(chunks))
    assert out == b"The quick brown fox jumps over the lazy dog."


def test_chunked_redaction_does_not_buffer_entire_stream():
    """Confirms the redactor is genuinely streaming — it emits early
    chunks before EOF and only retains a bounded tail. We feed enough
    data to force more than one emit cycle and assert intermediate
    output is produced."""
    overlap = OutputFilter._CHUNK_TAIL_OVERLAP_BYTES
    # Two chunks of (overlap * 2) each → must produce intermediate output.
    big_chunk = (b"x" * (overlap * 2))
    chunks = [big_chunk, big_chunk]
    emissions = list(OutputFilter.redact_chunked(chunks))
    assert len(emissions) >= 2, (
        f"streaming redactor should yield multiple emissions; got {len(emissions)}"
    )
    # No data lost or duplicated.
    assert b"".join(emissions) == big_chunk + big_chunk


def test_chunked_redaction_with_aadhaar_split():
    chunks = [b"Aadhaar 1234 ", b"5678 9012 confirmed."]
    out = b"".join(OutputFilter.redact_chunked(chunks))
    assert b"1234 5678 9012" not in out
    assert b"AADHAAR_REDACTED" in out


# ---------------------------------------------------------------------------
# Composite InferenceProxy.filter_output happy path
# ---------------------------------------------------------------------------


def test_inference_proxy_filter_output_redacts_multiple_kinds():
    body = (
        "Customer admin@aegis.example wrote in from +91-9876543210 about "
        "their JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.sig — "
        "their account uses AKIAIOSFODNN7EXAMPLE."
    ).encode()
    out = inference_proxy.filter_output(body).decode("utf-8", errors="replace")
    assert "admin@aegis.example" not in out
    assert "9876543210" not in out
    assert "eyJ" not in out  # JWT prefix scrubbed
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "***EMAIL_REDACTED***" in out
    assert "***IN_PHONE_REDACTED***" in out
    assert "AWS_KEY_REDACTED" in out
