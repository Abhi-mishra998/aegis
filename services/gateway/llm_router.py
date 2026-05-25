"""
ACP Multi-LLM Router
====================
Provider-agnostic LLM inference proxy with per-provider cost caps, timeout
budgets, and automatic fallback.

Supported providers: groq | openai | anthropic | azure_openai
Config: LLM_PROVIDER env var (default: groq for backward compat)
        LLM_FALLBACK_PROVIDER: optional secondary if primary fails
        Per-provider keys: OPENAI_API_KEY, ANTHROPIC_API_KEY, AZURE_OPENAI_* etc.

WIRING STATUS: This module provides the routing abstraction layer but is NOT
yet called from the gateway inference proxy (SecurityMiddleware).  The gateway
still calls the Groq SDK directly via services/insight/.  Integrating this
router into the hot path requires replacing those direct calls with
`get_llm_router().route(...)` — a deliberate wiring step that should be gated
on a feature flag and load-tested before enabling in production.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# EXCEPTIONS
# ---------------------------------------------------------------------------


class CostCapError(Exception):
    """Raised when a per-tenant daily LLM cost cap would be exceeded."""

    def __init__(self, provider: str, tenant_id: str, cap_usd: float, current_usd: float) -> None:
        self.provider = provider
        self.tenant_id = tenant_id
        self.cap_usd = cap_usd
        self.current_usd = current_usd
        super().__init__(
            f"Daily LLM cost cap of ${cap_usd:.4f} exceeded for tenant {tenant_id} "
            f"on provider {provider} (current: ${current_usd:.4f})"
        )


class LLMProviderError(Exception):
    """Raised when an LLM provider call fails."""


# ---------------------------------------------------------------------------
# RESPONSE TYPE
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Normalised response from any LLM provider."""

    text: str
    tokens_used: int
    model: str
    provider: str
    latency_ms: float


# ---------------------------------------------------------------------------
# ABSTRACT BASE PROVIDER
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Abstract base for all LLM provider implementations."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        """
        Send a chat-completion request and return a normalised LLMResponse.

        Parameters
        ----------
        messages:   OpenAI-style list of {role, content} dicts.
        model:      Provider-specific model identifier.
        max_tokens: Upper bound on generated tokens.
        timeout:    Request timeout in seconds.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short provider identifier, e.g. 'groq'."""

    @property
    @abstractmethod
    def cost_per_1k_tokens(self) -> float:
        """Approximate blended cost in USD per 1 000 tokens (input+output)."""


# ---------------------------------------------------------------------------
# GROQ PROVIDER
# ---------------------------------------------------------------------------


class GroqProvider(LLMProvider):
    """Groq Cloud provider — uses the `groq` Python package."""

    # Approximate blended cost for Llama-3 family on Groq (very cheap)
    _COST_PER_1K = 0.0001

    def __init__(self, api_key: str, default_model: str = "llama-3.1-8b-instant") -> None:
        self._api_key = api_key
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "groq"

    @property
    def cost_per_1k_tokens(self) -> float:
        return self._COST_PER_1K

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        try:
            import groq as groq_sdk  # optional server dependency
        except ImportError as exc:
            raise LLMProviderError(
                "groq package is not installed; add 'groq' to server extras"
            ) from exc

        effective_model = model or self._default_model
        t0 = time.monotonic()
        try:
            client = groq_sdk.AsyncGroq(api_key=self._api_key)
            resp = await client.chat.completions.create(
                messages=messages,  # type: ignore[arg-type]
                model=effective_model,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            raise LLMProviderError(f"Groq request failed: {exc}") from exc

        latency_ms = (time.monotonic() - t0) * 1000
        choice = resp.choices[0]
        text = choice.message.content or ""
        tokens_used = getattr(resp.usage, "total_tokens", 0) or 0

        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            model=effective_model,
            provider=self.name,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# OPENAI PROVIDER (httpx — no openai package)
# ---------------------------------------------------------------------------


class OpenAIProvider(LLMProvider):
    """OpenAI provider — calls REST API directly via httpx."""

    _API_URL = "https://api.openai.com/v1/chat/completions"
    # gpt-4o-mini blended input+output cost at time of writing
    _COST_PER_1K = 0.00015

    def __init__(self, api_key: str, default_model: str = "gpt-4o-mini") -> None:
        self._api_key = api_key
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "openai"

    @property
    def cost_per_1k_tokens(self) -> float:
        return self._COST_PER_1K

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        effective_model = model or self._default_model
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._API_URL, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"OpenAI HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise LLMProviderError(f"OpenAI request failed: {exc}") from exc

        latency_ms = (time.monotonic() - t0) * 1000
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        tokens_used = data.get("usage", {}).get("total_tokens", 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            model=effective_model,
            provider=self.name,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# ANTHROPIC PROVIDER (httpx — no anthropic package)
# ---------------------------------------------------------------------------


class AnthropicProvider(LLMProvider):
    """Anthropic provider — calls Messages API directly via httpx."""

    _API_URL = "https://api.anthropic.com/v1/messages"
    _ANTHROPIC_VERSION = "2023-06-01"
    # claude-haiku-4-5 blended cost
    _COST_PER_1K = 0.0003

    def __init__(self, api_key: str, default_model: str = "claude-haiku-4-5-20251001") -> None:
        self._api_key = api_key
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def cost_per_1k_tokens(self) -> float:
        return self._COST_PER_1K

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        effective_model = model or self._default_model

        # Anthropic separates the system message from the messages array
        system_content: str = ""
        user_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system_content = msg.get("content", "")
            else:
                user_messages.append({"role": msg["role"], "content": msg.get("content", "")})

        payload: dict[str, Any] = {
            "model": effective_model,
            "max_tokens": max_tokens,
            "messages": user_messages,
        }
        if system_content:
            payload["system"] = system_content

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._API_URL, json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Anthropic HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise LLMProviderError(f"Anthropic request failed: {exc}") from exc

        latency_ms = (time.monotonic() - t0) * 1000
        data = resp.json()
        # Anthropic returns content as a list of blocks
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text = block.get("text", "")
                break
        usage = data.get("usage", {})
        tokens_used = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            model=effective_model,
            provider=self.name,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# AZURE OPENAI PROVIDER (httpx)
# ---------------------------------------------------------------------------


class AzureOpenAIProvider(LLMProvider):
    """Azure OpenAI provider — calls Azure REST API directly via httpx."""

    # Azure pricing varies by deployment; use gpt-4o-mini as approximate default
    _COST_PER_1K = 0.000165

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str = "2024-02-01",
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._deployment = deployment
        self._api_version = api_version

    @property
    def name(self) -> str:
        return "azure_openai"

    @property
    def cost_per_1k_tokens(self) -> float:
        return self._COST_PER_1K

    def _build_url(self) -> str:
        return (
            f"{self._endpoint}/openai/deployments/{self._deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
    ) -> LLMResponse:
        # Azure ignores the model field — deployment is the model selector
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
        }
        headers = {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(self._build_url(), json=payload, headers=headers)
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Azure OpenAI HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except Exception as exc:
            raise LLMProviderError(f"Azure OpenAI request failed: {exc}") from exc

        latency_ms = (time.monotonic() - t0) * 1000
        data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        tokens_used = data.get("usage", {}).get("total_tokens", 0)

        return LLMResponse(
            text=text,
            tokens_used=tokens_used,
            model=self._deployment,
            provider=self.name,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# LLM ROUTER
# ---------------------------------------------------------------------------


class LLMRouter:
    """
    Routes LLM inference requests to the configured primary provider with
    optional fallback, per-provider cost tracking via Redis, and daily cost caps.
    """

    def __init__(
        self,
        provider_name: str,
        fallback_provider_name: str = "",
        daily_cost_cap_usd: float = 0.0,
        *,
        # Injected for testing; in production these come from settings
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        anthropic_api_key: str = "",
        anthropic_model: str = "claude-haiku-4-5-20251001",
        groq_api_key: str = "",
        groq_model: str = "llama-3.1-8b-instant",
        azure_endpoint: str = "",
        azure_api_key: str = "",
        azure_deployment: str = "",
        azure_api_version: str = "2024-02-01",
        redis_client: Any = None,
    ) -> None:
        self._daily_cost_cap_usd = daily_cost_cap_usd
        self._redis = redis_client

        self._providers: dict[str, LLMProvider] = {}

        # Build all configured providers
        if groq_api_key:
            self._providers["groq"] = GroqProvider(
                api_key=groq_api_key, default_model=groq_model
            )
        if openai_api_key:
            self._providers["openai"] = OpenAIProvider(
                api_key=openai_api_key, default_model=openai_model
            )
        if anthropic_api_key:
            self._providers["anthropic"] = AnthropicProvider(
                api_key=anthropic_api_key, default_model=anthropic_model
            )
        if azure_endpoint and azure_api_key and azure_deployment:
            self._providers["azure_openai"] = AzureOpenAIProvider(
                endpoint=azure_endpoint,
                api_key=azure_api_key,
                deployment=azure_deployment,
                api_version=azure_api_version,
            )

        self._primary_name = provider_name
        self._fallback_name = fallback_provider_name

    def _get_provider(self, name: str) -> LLMProvider | None:
        return self._providers.get(name)

    def _today_str(self) -> str:
        return datetime.now(tz=UTC).strftime("%Y-%m-%d")

    async def _check_cost_cap(
        self, provider: LLMProvider, tenant_id: str
    ) -> None:
        """Raise CostCapError if the tenant's daily cost budget is exhausted."""
        if self._daily_cost_cap_usd <= 0.0 or self._redis is None:
            return

        key = f"acp:llm_cost:{provider.name}:{tenant_id}:{self._today_str()}"
        try:
            raw = await self._redis.get(key)
            current = float(raw) if raw else 0.0
        except Exception:
            # Redis unavailable — don't block requests
            return

        if current >= self._daily_cost_cap_usd:
            raise CostCapError(
                provider=provider.name,
                tenant_id=tenant_id,
                cap_usd=self._daily_cost_cap_usd,
                current_usd=current,
            )

    async def _record_cost(
        self, provider: LLMProvider, tenant_id: str, tokens_used: int
    ) -> None:
        """Atomically add the call's cost to the tenant's daily counter."""
        if self._redis is None:
            return

        cost_usd = (tokens_used / 1000.0) * provider.cost_per_1k_tokens
        key = f"acp:llm_cost:{provider.name}:{tenant_id}:{self._today_str()}"
        try:
            await self._redis.incrbyfloat(key, cost_usd)
            # Expire after 48h to bound Redis memory
            await self._redis.expire(key, 48 * 3600)
        except Exception as exc:
            logger.warning("llm_cost_record_failed", error=str(exc))

    async def route(
        self,
        messages: list[dict[str, Any]],
        model: str = "",
        max_tokens: int = 512,
        timeout: float = 5.0,
        tenant_id: str = "default",
    ) -> LLMResponse:
        """
        Route the inference request to the primary provider; fall back to
        secondary on failure.  Tracks cost in Redis and enforces daily caps.

        Parameters
        ----------
        messages:   OpenAI-style list of {role, content} dicts.
        model:      Override provider default model.
        max_tokens: Max tokens to generate.
        timeout:    Per-provider network timeout in seconds.
        tenant_id:  Used as part of the cost-tracking Redis key.
        """
        primary = self._get_provider(self._primary_name)
        fallback = self._get_provider(self._fallback_name) if self._fallback_name else None

        if primary is None:
            # Provider not configured — try fallback immediately
            if fallback is None:
                raise LLMProviderError(
                    f"No provider configured for '{self._primary_name}' "
                    f"(and no fallback). Configure API keys."
                )
            return await self._call_provider(fallback, messages, model, max_tokens, timeout, tenant_id)

        # Check cost cap before attempting the primary call
        await self._check_cost_cap(primary, tenant_id)

        try:
            response = await self._call_provider(
                primary, messages, model, max_tokens, timeout, tenant_id
            )
            return response
        except CostCapError:
            raise
        except LLMProviderError as exc:
            if fallback is None:
                raise
            logger.warning(
                "llm_primary_failed_using_fallback",
                primary=self._primary_name,
                fallback=self._fallback_name,
                error=str(exc),
            )
            # Check cost cap on fallback before attempting
            await self._check_cost_cap(fallback, tenant_id)
            return await self._call_provider(
                fallback, messages, model, max_tokens, timeout, tenant_id
            )

    async def _call_provider(
        self,
        provider: LLMProvider,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        timeout: float,
        tenant_id: str,
    ) -> LLMResponse:
        t0 = time.monotonic()
        response = await provider.complete(
            messages=messages,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        elapsed_ms = (time.monotonic() - t0) * 1000

        logger.info(
            "llm_request_completed",
            provider=provider.name,
            model=response.model,
            tokens_used=response.tokens_used,
            latency_ms=round(elapsed_ms, 1),
            tenant_id=tenant_id,
        )

        await self._record_cost(provider, tenant_id, response.tokens_used)
        return response


# ---------------------------------------------------------------------------
# MODULE-LEVEL SINGLETON + FACTORY
# ---------------------------------------------------------------------------

router_singleton: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """
    Return the module-level LLMRouter singleton, constructing it on first call
    from ACPSettings / environment variables.
    """
    global router_singleton
    if router_singleton is not None:
        return router_singleton

    from sdk.common.config import settings  # late import to avoid circular deps

    router_singleton = LLMRouter(
        provider_name=settings.LLM_PROVIDER,
        fallback_provider_name=settings.LLM_FALLBACK_PROVIDER,
        daily_cost_cap_usd=settings.LLM_DAILY_COST_CAP_USD,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_MODEL,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        anthropic_model=settings.ANTHROPIC_MODEL,
        groq_api_key=settings.GROQ_API_KEY,
        groq_model=settings.GROQ_MODEL_FAST,
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        azure_api_key=settings.AZURE_OPENAI_API_KEY,
        azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT,
        azure_api_version=settings.AZURE_OPENAI_API_VERSION,
    )
    return router_singleton
