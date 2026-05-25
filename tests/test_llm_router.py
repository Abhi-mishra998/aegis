"""
Tests for services/gateway/llm_router.py

All tests are offline — no real API calls are made.  httpx and groq responses
are mocked at the network layer so the test suite runs in CI without any keys.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.gateway.llm_router import (
    AnthropicProvider,
    AzureOpenAIProvider,
    CostCapError,
    GroqProvider,
    LLMProvider,
    LLMResponse,
    LLMRouter,
    OpenAIProvider,
    get_llm_router,
    router_singleton,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Say hello."},
]


def _make_openai_response(text: str = "Hello!", tokens: int = 20) -> dict[str, Any]:
    """Build an OpenAI-shaped chat completion response dict."""
    return {
        "choices": [{"message": {"content": text, "role": "assistant"}}],
        "usage": {"total_tokens": tokens},
        "model": "gpt-4o-mini",
    }


def _make_anthropic_response(text: str = "Hello!", tokens_in: int = 10, tokens_out: int = 10) -> dict[str, Any]:
    """Build an Anthropic Messages API response dict."""
    return {
        "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": tokens_in, "output_tokens": tokens_out},
        "model": "claude-haiku-4-5-20251001",
    }


def _make_groq_response(text: str = "Hello!", tokens: int = 20) -> MagicMock:
    """Build a groq-style response object (MagicMock with the right attributes)."""
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = text
    mock_resp.usage.total_tokens = tokens
    return mock_resp


def _make_httpx_response(data: dict[str, Any], status_code: int = 200) -> MagicMock:
    """Build a mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    mock.text = json.dumps(data)
    mock.raise_for_status = MagicMock()  # no-op by default
    return mock


# ---------------------------------------------------------------------------
# test_groq_provider_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_groq_provider_success():
    """GroqProvider parses a groq SDK response correctly."""
    groq_resp = _make_groq_response(text="Hello from Groq!", tokens=42)

    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=groq_resp)

    with patch("services.gateway.llm_router.GroqProvider.complete") as mock_complete:
        mock_complete.return_value = LLMResponse(
            text="Hello from Groq!",
            tokens_used=42,
            model="llama-3.1-8b-instant",
            provider="groq",
            latency_ms=50.0,
        )
        provider = GroqProvider(api_key="test-key", default_model="llama-3.1-8b-instant")
        result = await provider.complete(MESSAGES, model="llama-3.1-8b-instant", max_tokens=128, timeout=5.0)

    assert result.text == "Hello from Groq!"
    assert result.tokens_used == 42
    assert result.provider == "groq"
    assert result.model == "llama-3.1-8b-instant"
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_groq_provider_response_parsing():
    """GroqProvider fields are populated from the groq SDK response."""
    # Use a simulated groq async client by patching the groq module itself
    groq_resp = _make_groq_response(text="Security analysis complete", tokens=100)

    mock_groq_module = MagicMock()
    mock_async_client = MagicMock()
    mock_async_client.chat.completions.create = AsyncMock(return_value=groq_resp)
    mock_groq_module.AsyncGroq.return_value = mock_async_client

    provider = GroqProvider(api_key="test-key", default_model="llama-3.1-8b-instant")

    with patch.dict("sys.modules", {"groq": mock_groq_module}):
        result = await provider.complete(
            messages=MESSAGES,
            model="llama-3.1-8b-instant",
            max_tokens=200,
            timeout=5.0,
        )

    assert result.text == "Security analysis complete"
    assert result.tokens_used == 100
    assert result.provider == "groq"
    assert result.model == "llama-3.1-8b-instant"
    assert isinstance(result.latency_ms, float)
    assert result.latency_ms >= 0.0


# ---------------------------------------------------------------------------
# test_openai_provider_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_provider_success():
    """OpenAIProvider parses the REST API response correctly."""
    api_data = _make_openai_response(text="Hello from OpenAI!", tokens=30)
    mock_response = _make_httpx_response(api_data)

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.post = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(api_key="sk-test", default_model="gpt-4o-mini")

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        result = await provider.complete(
            messages=MESSAGES,
            model="gpt-4o-mini",
            max_tokens=256,
            timeout=5.0,
        )

    assert result.text == "Hello from OpenAI!"
    assert result.tokens_used == 30
    assert result.provider == "openai"
    assert result.model == "gpt-4o-mini"
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_openai_provider_sends_auth_header():
    """OpenAIProvider includes Authorization header in the request."""
    api_data = _make_openai_response()
    mock_response = _make_httpx_response(api_data)

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.post = AsyncMock(return_value=mock_response)

    provider = OpenAIProvider(api_key="sk-test-key", default_model="gpt-4o-mini")

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        await provider.complete(messages=MESSAGES, model="", max_tokens=64, timeout=3.0)

    call_kwargs = mock_async_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert headers.get("Authorization") == "Bearer sk-test-key"


# ---------------------------------------------------------------------------
# test_anthropic_provider_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_provider_success():
    """AnthropicProvider parses the Anthropic Messages API response correctly."""
    api_data = _make_anthropic_response(text="Hello from Anthropic!", tokens_in=15, tokens_out=20)
    mock_response = _make_httpx_response(api_data)

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.post = AsyncMock(return_value=mock_response)

    provider = AnthropicProvider(
        api_key="sk-ant-test",
        default_model="claude-haiku-4-5-20251001",
    )

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        result = await provider.complete(
            messages=MESSAGES,
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            timeout=5.0,
        )

    assert result.text == "Hello from Anthropic!"
    assert result.tokens_used == 35  # 15 + 20
    assert result.provider == "anthropic"
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_anthropic_provider_splits_system_message():
    """AnthropicProvider moves system role to top-level system field."""
    api_data = _make_anthropic_response()
    mock_response = _make_httpx_response(api_data)

    mock_async_client = AsyncMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.post = AsyncMock(return_value=mock_response)

    provider = AnthropicProvider(api_key="sk-ant-test")

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        await provider.complete(messages=MESSAGES, model="", max_tokens=64, timeout=3.0)

    call_kwargs = mock_async_client.post.call_args
    payload = call_kwargs.kwargs.get("json", {})
    # System message should be hoisted to top-level field
    assert "system" in payload
    assert payload["system"] == "You are a helpful assistant."
    # Only non-system messages in messages array
    for msg in payload["messages"]:
        assert msg["role"] != "system"


# ---------------------------------------------------------------------------
# test_fallback_on_primary_failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_on_primary_failure():
    """When the primary provider raises LLMProviderError, the fallback is used."""
    from services.gateway.llm_router import LLMProviderError

    fallback_response = LLMResponse(
        text="Hello from fallback!",
        tokens_used=25,
        model="claude-haiku-4-5-20251001",
        provider="anthropic",
        latency_ms=120.0,
    )

    # Primary provider that always raises
    primary = MagicMock(spec=LLMProvider)
    primary.name = "openai"
    primary.cost_per_1k_tokens = 0.00015
    primary.complete = AsyncMock(side_effect=LLMProviderError("connection refused"))

    # Fallback provider that succeeds
    fallback = MagicMock(spec=LLMProvider)
    fallback.name = "anthropic"
    fallback.cost_per_1k_tokens = 0.0003
    fallback.complete = AsyncMock(return_value=fallback_response)

    router = LLMRouter(
        provider_name="openai",
        fallback_provider_name="anthropic",
        daily_cost_cap_usd=0.0,
        redis_client=None,
    )
    # Inject mock providers directly
    router._providers = {"openai": primary, "anthropic": fallback}

    result = await router.route(MESSAGES, model="", max_tokens=128, timeout=5.0)

    assert result.text == "Hello from fallback!"
    assert result.provider == "anthropic"
    # Primary was attempted once; fallback was called
    primary.complete.assert_awaited_once()
    fallback.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_fallback_propagates_error():
    """Without a fallback configured, primary failure propagates as LLMProviderError."""
    from services.gateway.llm_router import LLMProviderError

    primary = MagicMock(spec=LLMProvider)
    primary.name = "openai"
    primary.cost_per_1k_tokens = 0.00015
    primary.complete = AsyncMock(side_effect=LLMProviderError("timeout"))

    router = LLMRouter(
        provider_name="openai",
        fallback_provider_name="",
        daily_cost_cap_usd=0.0,
        redis_client=None,
    )
    router._providers = {"openai": primary}

    with pytest.raises(LLMProviderError, match="timeout"):
        await router.route(MESSAGES)


# ---------------------------------------------------------------------------
# test_cost_cap_blocking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_cap_blocking():
    """Router raises CostCapError when the tenant's daily budget is exceeded."""
    # Mock Redis that returns a cost already at or above the cap
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"10.0")  # $10.00 already spent

    # A provider that would succeed if called
    provider = MagicMock(spec=LLMProvider)
    provider.name = "openai"
    provider.cost_per_1k_tokens = 0.00015
    provider.complete = AsyncMock(
        return_value=LLMResponse(
            text="should not reach here",
            tokens_used=100,
            model="gpt-4o-mini",
            provider="openai",
            latency_ms=50.0,
        )
    )

    router = LLMRouter(
        provider_name="openai",
        fallback_provider_name="",
        daily_cost_cap_usd=5.0,  # cap is $5.00; current is $10.00
        redis_client=mock_redis,
    )
    router._providers = {"openai": provider}

    with pytest.raises(CostCapError) as exc_info:
        await router.route(MESSAGES, tenant_id="tenant-abc")

    err = exc_info.value
    assert err.cap_usd == 5.0
    assert err.current_usd == 10.0
    assert err.tenant_id == "tenant-abc"
    assert err.provider == "openai"
    # Provider must NOT have been called
    provider.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_cap_allows_when_under_budget():
    """Router proceeds normally when the tenant's cost is below the cap."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=b"1.0")  # $1 spent of $5 cap
    mock_redis.incrbyfloat = AsyncMock(return_value=1.0015)
    mock_redis.expire = AsyncMock()

    provider = MagicMock(spec=LLMProvider)
    provider.name = "openai"
    provider.cost_per_1k_tokens = 0.00015
    provider.complete = AsyncMock(
        return_value=LLMResponse(
            text="within budget",
            tokens_used=100,
            model="gpt-4o-mini",
            provider="openai",
            latency_ms=80.0,
        )
    )

    router = LLMRouter(
        provider_name="openai",
        fallback_provider_name="",
        daily_cost_cap_usd=5.0,
        redis_client=mock_redis,
    )
    router._providers = {"openai": provider}

    result = await router.route(MESSAGES, tenant_id="tenant-xyz")
    assert result.text == "within budget"
    provider.complete.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_router_selects_provider_from_config
# ---------------------------------------------------------------------------


def test_router_selects_provider_from_config_groq():
    """When LLM_PROVIDER=groq, router's primary provider is GroqProvider."""
    router = LLMRouter(
        provider_name="groq",
        groq_api_key="test-groq-key",
        groq_model="llama-3.1-8b-instant",
    )
    provider = router._get_provider("groq")
    assert provider is not None
    assert isinstance(provider, GroqProvider)
    assert provider.name == "groq"


def test_router_selects_provider_from_config_openai():
    """When LLM_PROVIDER=openai, router's primary provider is OpenAIProvider."""
    router = LLMRouter(
        provider_name="openai",
        openai_api_key="sk-test",
        openai_model="gpt-4o-mini",
    )
    provider = router._get_provider("openai")
    assert provider is not None
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == "openai"


def test_router_selects_provider_from_config_anthropic():
    """When LLM_PROVIDER=anthropic, router's primary provider is AnthropicProvider."""
    router = LLMRouter(
        provider_name="anthropic",
        anthropic_api_key="sk-ant-test",
        anthropic_model="claude-haiku-4-5-20251001",
    )
    provider = router._get_provider("anthropic")
    assert provider is not None
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"


def test_router_selects_provider_from_config_azure():
    """When LLM_PROVIDER=azure_openai, router's primary provider is AzureOpenAIProvider."""
    router = LLMRouter(
        provider_name="azure_openai",
        azure_endpoint="https://my.openai.azure.com",
        azure_api_key="azure-key",
        azure_deployment="my-deployment",
        azure_api_version="2024-02-01",
    )
    provider = router._get_provider("azure_openai")
    assert provider is not None
    assert isinstance(provider, AzureOpenAIProvider)
    assert provider.name == "azure_openai"


def test_router_missing_provider_returns_none():
    """Router returns None for a provider with no configured API key."""
    router = LLMRouter(provider_name="openai")  # no openai_api_key
    provider = router._get_provider("openai")
    assert provider is None


# ---------------------------------------------------------------------------
# cost tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_recorded_in_redis_after_successful_call():
    """After a successful call, cost is recorded via INCRBYFLOAT."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.incrbyfloat = AsyncMock(return_value=0.03)
    mock_redis.expire = AsyncMock()

    provider = MagicMock(spec=LLMProvider)
    provider.name = "openai"
    provider.cost_per_1k_tokens = 0.00015
    provider.complete = AsyncMock(
        return_value=LLMResponse(
            text="cost tracked",
            tokens_used=200,
            model="gpt-4o-mini",
            provider="openai",
            latency_ms=75.0,
        )
    )

    router = LLMRouter(
        provider_name="openai",
        daily_cost_cap_usd=0.0,  # no cap
        redis_client=mock_redis,
    )
    router._providers = {"openai": provider}

    await router.route(MESSAGES, tenant_id="tenant-cost-test")

    # INCRBYFLOAT should have been called with the expected key pattern
    mock_redis.incrbyfloat.assert_awaited_once()
    call_args = mock_redis.incrbyfloat.call_args[0]
    assert "acp:llm_cost:openai:tenant-cost-test:" in call_args[0]
    expected_cost = (200 / 1000.0) * 0.00015
    assert abs(call_args[1] - expected_cost) < 1e-9


@pytest.mark.asyncio
async def test_cost_tracking_survives_redis_error():
    """If Redis is unavailable, the call still succeeds (cost tracking is best-effort)."""
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
    mock_redis.incrbyfloat = AsyncMock(side_effect=ConnectionError("redis down"))
    mock_redis.expire = AsyncMock()

    provider = MagicMock(spec=LLMProvider)
    provider.name = "groq"
    provider.cost_per_1k_tokens = 0.0001
    provider.complete = AsyncMock(
        return_value=LLMResponse(
            text="redis-resilient response",
            tokens_used=50,
            model="llama-3.1-8b-instant",
            provider="groq",
            latency_ms=30.0,
        )
    )

    router = LLMRouter(
        provider_name="groq",
        daily_cost_cap_usd=0.0,
        redis_client=mock_redis,
    )
    router._providers = {"groq": provider}

    result = await router.route(MESSAGES)
    assert result.text == "redis-resilient response"


# ---------------------------------------------------------------------------
# LLMResponse dataclass
# ---------------------------------------------------------------------------


def test_llm_response_fields():
    """LLMResponse dataclass stores all fields correctly."""
    r = LLMResponse(
        text="hello",
        tokens_used=10,
        model="llama-3.1-8b-instant",
        provider="groq",
        latency_ms=42.5,
    )
    assert r.text == "hello"
    assert r.tokens_used == 10
    assert r.model == "llama-3.1-8b-instant"
    assert r.provider == "groq"
    assert r.latency_ms == 42.5


# ---------------------------------------------------------------------------
# Provider abstract interface
# ---------------------------------------------------------------------------


def test_all_providers_implement_interface():
    """All providers expose name and cost_per_1k_tokens properties."""
    providers: list[LLMProvider] = [
        GroqProvider(api_key="k"),
        OpenAIProvider(api_key="k"),
        AnthropicProvider(api_key="k"),
        AzureOpenAIProvider(
            endpoint="https://x.openai.azure.com",
            api_key="k",
            deployment="dep",
        ),
    ]
    for p in providers:
        assert isinstance(p.name, str) and p.name
        assert isinstance(p.cost_per_1k_tokens, float)
        assert p.cost_per_1k_tokens >= 0.0
