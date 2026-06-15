"""
Unit tests for services/registry/wizard.py.

Covers the parts of the wizard that don't need a live DB:
  - WizardCreateRequest schema (name normalization + risk-level validation).
  - _build_snippet for all 8 providers — shape + contents + the
    non-negotiable "no customer LLM key value in the snippet" rule.
  - _DEFAULT_TOOL_WHITELIST has exactly 8 entries (matches the wizard
    Step 2 "8 standard tools" claim).
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from services.registry.wizard import (
    _DEFAULT_TOOL_WHITELIST,
    InstallSnippetResponse,
    Provider,
    WizardCreateRequest,
    _build_snippet,
)


# Pinned IDs so snippet content is deterministic across test runs.
TENANT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
AGENT_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
API_KEY = "acp_DEMO_KEY_FOR_TEST_NOT_REAL_xxxxxxxxxxxxxx"


PROVIDERS: tuple[Provider, ...] = (
    "anthropic", "openai", "bedrock", "langchain",
    "cursor", "claude-code", "openhands", "custom",
)


# ───────────────────────────────────────────────────────────────────────
# WizardCreateRequest schema
# ───────────────────────────────────────────────────────────────────────


def test_wizard_request_normalizes_name_to_lower_kebab():
    req = WizardCreateRequest(name="Finance Bot", provider="anthropic")
    assert req.name == "finance-bot"


def test_wizard_request_rejects_short_name():
    with pytest.raises(ValidationError):
        WizardCreateRequest(name="ab", provider="anthropic")


def test_wizard_request_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        WizardCreateRequest(name="finance-bot", provider="rogue-provider")


def test_wizard_request_defaults_risk_to_medium():
    req = WizardCreateRequest(name="finance-bot", provider="anthropic")
    assert req.risk_level == "medium"


def test_wizard_request_accepts_low_medium_high_only():
    for ok in ("low", "medium", "high"):
        WizardCreateRequest(name="finance-bot", provider="anthropic", risk_level=ok)
    with pytest.raises(ValidationError):
        WizardCreateRequest(
            name="finance-bot", provider="anthropic", risk_level="critical",
        )


# ───────────────────────────────────────────────────────────────────────
# _DEFAULT_TOOL_WHITELIST
# ───────────────────────────────────────────────────────────────────────


def test_default_whitelist_has_exactly_8_tools():
    assert len(_DEFAULT_TOOL_WHITELIST) == 8
    assert len(set(_DEFAULT_TOOL_WHITELIST)) == 8  # no dupes


# ───────────────────────────────────────────────────────────────────────
# _build_snippet — per-provider shape
# ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_snippet_basic_shape(provider):
    snippet = _build_snippet(
        provider, tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY,
    )
    assert isinstance(snippet, InstallSnippetResponse)
    assert snippet.provider == provider
    assert snippet.snippet  # non-empty
    assert snippet.install_command  # non-empty
    assert isinstance(snippet.env_vars, list) and snippet.env_vars
    assert isinstance(snippet.notes, list)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_snippet_always_contains_aegis_identifiers(provider):
    snippet = _build_snippet(
        provider, tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY,
    )
    body = snippet.snippet
    assert API_KEY in body, f"{provider}: aegis_api_key missing from snippet"
    assert str(TENANT_ID) in body, f"{provider}: tenant_id missing from snippet"
    assert str(AGENT_ID) in body, f"{provider}: agent_id missing from snippet"


# This is the non-negotiable security guarantee. PRODUCT_PLAN.md §1.3:
# "The customer's Claude / OpenAI / Bedrock key stays on the customer's
# machine. Aegis never sees, stores, or asks for that key."
_FORBIDDEN_LLM_KEY_PATTERNS = (
    "sk-ant-",     # Anthropic
    "sk-proj-",    # OpenAI projects
    "sk-",         # OpenAI legacy (will also match Anthropic; that's fine — both forbidden)
    "AKIA",        # AWS access key prefix
    "ASIA",        # AWS temporary access key prefix
)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_snippet_never_contains_a_real_llm_key_value(provider):
    snippet = _build_snippet(
        provider, tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY,
    )
    body = snippet.snippet
    for forbidden in _FORBIDDEN_LLM_KEY_PATTERNS:
        # Note: it's fine for the snippet to reference the ENV VAR name
        # (e.g. `export ANTHROPIC_API_KEY=...`) — what's forbidden is a
        # literal `sk-ant-XXX` value baked in. The check below proves we
        # never embed a real-looking key.
        assert forbidden not in body, (
            f"{provider}: snippet appears to embed a literal LLM key "
            f"value ('{forbidden}'). The customer's key MUST stay on "
            f"their machine."
        )


@pytest.mark.parametrize("provider", PROVIDERS)
def test_snippet_advertises_no_llm_key_capture(provider):
    """At least one note or code-comment must remind the customer their
    LLM-provider key is theirs. This is a soft contract — if a future
    edit accidentally strips the reassurance, this test catches it."""
    snippet = _build_snippet(
        provider, tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY,
    )
    combined = snippet.snippet.lower() + " ".join(snippet.notes).lower()
    keywords = ("your machine", "never leaves", "stays on", "never sees")
    assert any(k in combined for k in keywords), (
        f"{provider}: snippet must advertise that LLM keys stay client-side"
    )


# ───────────────────────────────────────────────────────────────────────
# Provider-specific assertions (small, high-signal)
# ───────────────────────────────────────────────────────────────────────


def test_anthropic_snippet_references_aegis_anthropic_sdk():
    s = _build_snippet("anthropic", tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY)
    assert "aegis_anthropic" in s.snippet
    assert "pip install aegis-anthropic" == s.install_command


def test_openai_snippet_references_aegis_openai_sdk():
    s = _build_snippet("openai", tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY)
    assert "aegis_openai" in s.snippet


def test_cursor_snippet_is_a_json_mcp_block():
    s = _build_snippet("cursor", tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY)
    assert "@aegis/mcp-server" in s.snippet
    assert s.language == "javascript"
    # Cursor flow has no LLM env var of its own — verify the env_var list
    # is exactly the 4 Aegis fields, no leakage.
    assert set(s.env_vars) == {
        "AEGIS_API_KEY", "AEGIS_TENANT_ID", "AEGIS_AGENT_ID", "AEGIS_ENDPOINT",
    }


def test_custom_snippet_is_a_curl_template():
    s = _build_snippet("custom", tenant_id=TENANT_ID, agent_id=AGENT_ID, api_key=API_KEY)
    assert "curl" in s.snippet
    assert "/execute" in s.snippet
