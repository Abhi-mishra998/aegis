"""
Tests for Day 5-6 SDK packages and Day 7-8 SSO/OIDC.

Day 5-6: Source-contract tests — verify the integration packages are properly structured.
Day 7-8: Source-contract + logic tests for OIDC helper functions.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Day 5-6: aegis-langchain
# ---------------------------------------------------------------------------

def test_langchain_package_has_aegis_middleware():
    src = Path("integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert "class AegisMiddleware" in src
    assert "class AegisClient" in src
    assert "class AegisCallbackHandler" in src


def test_langchain_middleware_patches_tools():
    src = Path("integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert "_patch_tools" in src
    assert "tool._run" in src


def test_langchain_middleware_blocks_on_deny():
    src = Path("integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert "is_blocked" in src
    assert "BLOCKED by Aegis" in src


def test_langchain_fail_open_on_network_error():
    src = Path("integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert '"action": "allow"' in src  # fail-open default on errors
    assert "aegis_error:" in src


# ---------------------------------------------------------------------------
# Day 5-6: aegis-openai
# ---------------------------------------------------------------------------

def test_openai_package_wraps_chat_completions():
    src = Path("integrations/aegis-openai/aegis_openai/__init__.py").read_text()
    assert "class AegisOpenAI" in src
    assert "_GovernedCompletions" in src
    assert "tool_calls" in src


def test_openai_blocked_calls_annotated_on_response():
    src = Path("integrations/aegis-openai/aegis_openai/__init__.py").read_text()
    assert "_aegis_blocked" in src


# ---------------------------------------------------------------------------
# Day 5-6: aegis-anthropic
# ---------------------------------------------------------------------------

def test_anthropic_package_intercepts_tool_use():
    src = Path("integrations/aegis-anthropic/aegis_anthropic/__init__.py").read_text()
    assert "class AegisAnthropic" in src
    assert "_GovernedMessages" in src
    assert "tool_use" in src


def test_anthropic_blocked_replaced_with_text_block():
    src = Path("integrations/aegis-anthropic/aegis_anthropic/__init__.py").read_text()
    assert "TextBlock" in src
    assert "BLOCKED by Aegis" in src


# ---------------------------------------------------------------------------
# Day 5-6: AegisClient logic (no network)
# ---------------------------------------------------------------------------

def test_aegis_client_is_blocked_returns_true_for_deny():
    sys.path.insert(0, str(Path("integrations/aegis-langchain")))
    from aegis_langchain import AegisClient

    client = AegisClient.__new__(AegisClient)
    assert client.is_blocked({"action": "deny"})
    assert client.is_blocked({"action": "block"})
    assert client.is_blocked({"action": "policy_deny"})
    assert not client.is_blocked({"action": "allow"})
    assert not client.is_blocked({})


def test_aegis_client_blocked_message_format():
    sys.path.insert(0, str(Path("integrations/aegis-langchain")))
    from aegis_langchain import AegisClient

    client = AegisClient.__new__(AegisClient)
    msg = client.blocked_message("run_sql", {"action": "deny", "risk": 0.95, "findings": ["sql_injection"]})
    assert "run_sql" in msg
    assert "0.950" in msg
    assert "sql_injection" in msg


# ---------------------------------------------------------------------------
# Day 7-8: SSO/OIDC
# ---------------------------------------------------------------------------

def test_oidc_module_has_enabled_providers():
    src = Path("services/identity/oidc.py").read_text()
    assert "def enabled_providers" in src
    assert "GOOGLE_CLIENT_ID" in src
    assert "MICROSOFT_CLIENT_ID" in src
    assert "OKTA_CLIENT_ID" in src


def test_oidc_module_has_exchange_code():
    src = Path("services/identity/oidc.py").read_text()
    assert "async def exchange_code" in src
    assert "token_endpoint" in src


def test_oidc_csrf_state_roundtrip():
    from services.identity.oidc import generate_state, verify_state

    secret = "test-secret-key"
    tid = "00000000-0000-0000-0000-000000000001"
    state = generate_state(secret, "google", tid)
    provider, tenant_id = verify_state(secret, state)
    assert provider == "google"
    assert tenant_id == tid


def test_oidc_csrf_rejects_tampered_state():
    from services.identity.oidc import generate_state, verify_state

    secret = "test-secret-key"
    state = generate_state(secret, "google", "tenant-id")
    tampered = state[:-4] + "xxxx"

    with pytest.raises(ValueError, match="mismatch|malformed|expired|failed"):
        verify_state(secret, tampered)


def test_oidc_csrf_rejects_expired_state():
    import hashlib
    import hmac as _hmac

    secret = "test-secret-key"
    old_ts = str(int(time.time()) - 700)
    tid = "tenant-id"
    msg = f"google|{tid}|{old_ts}"
    sig = _hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]
    expired_state = f"{msg}|{sig}"

    from services.identity.oidc import verify_state
    with pytest.raises(ValueError, match="expired"):
        verify_state(secret, expired_state, max_age=600)


def test_sso_routes_added_to_identity_router():
    src = Path("services/identity/router.py").read_text()
    assert "/auth/sso/providers" in src
    assert "/auth/sso/{provider}" in src
    assert "/auth/sso/{provider}/callback" in src


def test_sso_routes_skip_auth_in_middleware():
    src = Path("services/gateway/middleware.py").read_text()
    assert "/auth/sso/providers" in src
    assert "_SKIP_PATH_PREFIXES" in src
    assert '"/auth/sso/"' in src


def test_sso_proxy_routes_in_gateway():
    # /auth/sso/{provider} + callback extracted to routers/sso.py in sprint-5.
    src = (
        Path("services/gateway/main.py").read_text()
        + Path("services/gateway/routers/sso.py").read_text()
    )
    assert "/auth/sso/{provider}" in src
    assert "/auth/sso/{provider}/callback" in src


def test_login_jsx_has_sso_buttons():
    src = Path("ui/src/pages/Login.jsx").read_text()
    assert "getSSOProviders" in src
    assert "ssoProviders" in src
    assert "/auth/sso/google" in src
    assert "/auth/sso/microsoft" in src
    assert "/auth/sso/okta" in src
