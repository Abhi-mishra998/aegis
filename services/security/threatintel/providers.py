"""Sprint 7 — IOC ingestion adapters + orchestrator.

A `BaseProvider` defines the contract:

    async def collect(self) -> list[IOCRecord]

Three concrete providers ship in Sprint 7:

  * `StaticListProvider` — wraps an in-process list. Used to keep the
    Aegis hardcoded defaults (`_KNOWN_EXFIL_DESTS`, etc.) addressable as
    IOCs in the cache so operators can see + extend them through the
    same API.
  * `HttpFeedProvider` — pulls a URL on the orchestrator's schedule.
    Parses `text/plain` (one IOC per line, `#` comments) or
    `application/json` (array of {kind, value, severity} objects).
    Bounded retry on 5xx; 4xx fails fast. Response body is capped
    (streamed + aborted at `_HTTP_MAX_BYTES`) so a broken feed URL
    can't OOM the orchestrator worker — same defense as the OIDC and
    SCIM streamed-cap patterns.
  * `GlobalDefaultsProvider` — wraps the curated defaults that ship
    with the platform. Written to the `_global` tenant overlay so every
    tenant sees them without per-tenant seeding.

The orchestrator runs every configured provider for one tenant; a
failure in one is logged and the others still run. Same shape as the
Sprint 5 IAG ingestion.
"""
from __future__ import annotations

import abc
import asyncio
import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import structlog

from sdk.common.background import swallow_log

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Curated defaults — re-exported from the shared Sprint 8 pattern catalog
# so the cache-side providers and the canonical fast-path agree on what
# ships in the Aegis binary. An operator can disable an individual entry
# via DELETE /threat-intel/iocs/{id} once it's been seeded into Redis;
# this exports the *floor* the cache starts from.
# ---------------------------------------------------------------------------
from services.policy.pattern_catalog import (
    EXFIL_HOSTS as DEFAULT_EXFIL_HOSTS,
)
from services.policy.pattern_catalog import (
    OFFSHORE_TOKENS as DEFAULT_OFFSHORE_TOKENS,
)

from . import store
from .ioc import (
    KIND_EXFIL_HOST,
    KIND_OFFSHORE_TOKEN,
    SEV_HIGH,
    SOURCE_FEED,
    SOURCE_HARDCODED,
    IOCRecord,
    make_id,
)


class BaseProvider(abc.ABC):
    """Base class for every IOC ingestion adapter."""
    name: str = "base"

    @abc.abstractmethod
    async def collect(self) -> list[IOCRecord]:  # pragma: no cover
        ...


# ---------------------------------------------------------------------------
# Static list provider — wraps a Python iterable.
# ---------------------------------------------------------------------------
class StaticListProvider(BaseProvider):
    """Emit one IOCRecord per value in `values`."""

    def __init__(
        self, *, name: str, tenant_id: str, kind: str,
        values: Iterable[str], severity: str = SEV_HIGH,
        source: str = SOURCE_HARDCODED,
    ) -> None:
        self.name = name
        self._tenant_id = tenant_id
        self._kind = kind
        self._values = list(values)
        self._severity = severity
        self._source = source

    async def collect(self) -> list[IOCRecord]:
        ts = time.time()
        return [
            IOCRecord(
                id=make_id(self._tenant_id, self._kind, v),
                tenant_id=self._tenant_id,
                kind=self._kind,
                value=v,
                severity=self._severity,
                source=self._source,
                created_ts=ts,
                actor=self.name,
            )
            for v in self._values
        ]


def global_defaults_providers() -> list[BaseProvider]:
    """The default IOC set Aegis ships. Written to the GLOBAL tenant
    overlay so every tenant sees them without per-tenant seeding.

    Keep in sync with `services/policy/canonical.py:_KNOWN_EXFIL_DESTS` /
    `_OFFSHORE_TOKENS` — those constants remain in canonical.py as the
    hardcoded fallback when the cache is empty (rollback safety)."""
    return [
        StaticListProvider(
            name="aegis_default_exfil_hosts",
            tenant_id=store.GLOBAL_TENANT_ID,
            kind=KIND_EXFIL_HOST,
            values=DEFAULT_EXFIL_HOSTS,
        ),
        StaticListProvider(
            name="aegis_default_offshore_tokens",
            tenant_id=store.GLOBAL_TENANT_ID,
            kind=KIND_OFFSHORE_TOKEN,
            values=DEFAULT_OFFSHORE_TOKENS,
        ),
    ]


# ---------------------------------------------------------------------------
# HTTP feed provider — pulls a remote URL.
# ---------------------------------------------------------------------------
_HTTP_BACKOFF = (0.0, 0.5, 1.0, 2.0)
# Threatintel feeds are typically tens of KB to a few MB. 8 MiB gives
# ~100x headroom for the largest curated commercial feeds; anything
# past that is either a misconfigured URL, a broken feed vendor, or
# hostile content. Env-tunable so an unusually large legitimate feed
# can be admitted per deployment.
_HTTP_MAX_BYTES = int(os.getenv("THREATINTEL_MAX_FEED_BYTES", str(8 * 1024 * 1024)))


@dataclass(frozen=True)
class HttpFeedConfig:
    name:            str
    tenant_id:       str
    kind:            str
    url:             str
    format:          str = "text"     # "text" | "json"
    severity:        str = SEV_HIGH
    timeout_seconds: float = 5.0
    retries:         int = 3


class HttpFeedProvider(BaseProvider):
    """Pull an external feed via httpx.

    Construction takes the httpx client so the gateway can pass its
    shared pool in; tests can pass a fake.
    """

    def __init__(self, httpx_client: Any, cfg: HttpFeedConfig) -> None:
        self._client = httpx_client
        self._cfg = cfg
        self.name = cfg.name

    async def collect(self) -> list[IOCRecord]:
        body = await self._fetch_with_retry()
        if body is None:
            return []
        values = self._parse(body)
        ts = time.time()
        return [
            IOCRecord(
                id=make_id(self._cfg.tenant_id, self._cfg.kind, v),
                tenant_id=self._cfg.tenant_id,
                kind=self._cfg.kind,
                value=v,
                severity=self._cfg.severity,
                source=SOURCE_FEED,
                created_ts=ts,
                actor=self._cfg.name,
            )
            for v in values
        ]

    async def _fetch_with_retry(self) -> str | None:
        """Fetch the feed body with retry + streamed byte cap.

        Streaming (instead of plain .get()) lets us abort at
        _HTTP_MAX_BYTES before an oversize body ever fully materialises
        in RAM — a broken or hostile feed URL can't OOM the worker.
        Feed clients without .stream() (e.g. a MagicMock in tests) fall
        back to the plain .get() path.
        """
        attempts = self._cfg.retries + 1
        for i in range(attempts):
            if i > 0 and i < len(_HTTP_BACKOFF):
                await asyncio.sleep(_HTTP_BACKOFF[i])

            # Prefer streaming so we can enforce the byte cap.
            stream_fn = getattr(self._client, "stream", None)
            if stream_fn is not None:
                try:
                    async with stream_fn(
                        "GET", self._cfg.url,
                        timeout=self._cfg.timeout_seconds,
                    ) as resp:
                        code = getattr(resp, "status_code", 0)
                        if 400 <= code < 500:
                            return None
                        if not (200 <= code < 300):
                            continue
                        buf = bytearray()
                        async for chunk in resp.aiter_bytes():
                            buf.extend(chunk)
                            if len(buf) > _HTTP_MAX_BYTES:
                                logger.warning(
                                    "threatintel_feed_body_too_large",
                                    url=self._cfg.url,
                                    cap=_HTTP_MAX_BYTES,
                                )
                                return None
                        return bytes(buf).decode("utf-8", errors="replace")
                except Exception as exc:
                    swallow_log(
                        logger, "threatintel_http_fetch_failed", exc,
                        url=self._cfg.url,
                    )
                    continue

            # Fallback: no .stream() attribute — happens with test doubles.
            try:
                resp = await self._client.get(
                    self._cfg.url, timeout=self._cfg.timeout_seconds,
                )
            except Exception as exc:
                swallow_log(logger, "threatintel_http_fetch_failed", exc, url=self._cfg.url)
                continue
            code = getattr(resp, "status_code", 0)
            if 200 <= code < 300:
                text = getattr(resp, "text", "") or ""
                if len(text.encode("utf-8", errors="ignore")) > _HTTP_MAX_BYTES:
                    logger.warning(
                        "threatintel_feed_body_too_large",
                        url=self._cfg.url, cap=_HTTP_MAX_BYTES,
                    )
                    return None
                return text
            if 400 <= code < 500:
                return None
        return None

    def _parse(self, body: str) -> list[str]:
        body = body.strip()
        if not body:
            return []
        if self._cfg.format == "json":
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                return []
            if not isinstance(data, list):
                return []
            out: list[str] = []
            for item in data:
                if isinstance(item, str):
                    out.append(item)
                elif isinstance(item, dict) and "value" in item:
                    v = item.get("value")
                    if isinstance(v, str):
                        out.append(v)
            return out
        # text format — one IOC per line, `#` comments, trim whitespace.
        out = []
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            out.append(stripped)
        return out


# ---------------------------------------------------------------------------
# Orchestrator — runs every provider, batch-writes to Redis.
# ---------------------------------------------------------------------------
async def run_providers(
    redis: Any, providers: list[BaseProvider],
) -> dict[str, int]:
    """Run each provider, upsert its records, return {provider_name: count}.

    A provider raising an exception is logged-and-skipped, not fatal —
    same isolation contract the Sprint 5 IAG orchestrator gives.
    """
    summary: dict[str, int] = {}
    for prov in providers:
        try:
            records = await prov.collect()
        except Exception:
            summary[prov.name] = -1
            continue
        if not records:
            summary[prov.name] = 0
            continue
        # All records from a provider share a tenant; pick from the first.
        tenant_id = records[0].tenant_id
        n = await store.upsert_many(redis, tenant_id=tenant_id, records=records)
        await store.stamp_refresh(redis, tenant_id=tenant_id)
        summary[prov.name] = n
    return summary
