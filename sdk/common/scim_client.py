"""ATF v3.2 §4.2 SCIM adapter — outbound SCIM 2.0 client.

Small async wrapper around httpx that queries a customer's SCIM
directory for User / Agent resource state. Used by
`services/policy/scim_agent.reconcile()` to resolve every registered
agent's `human_responsible` reference against the live directory.

Design invariants:
  * Bearer token comes from a caller-supplied provider (per-tenant
    SSM/secret store); this module never reads env.
  * Every call has a hard timeout; a slow SCIM directory MUST NOT
    stall the reconciliation loop.
  * Response body is capped at `_SCIM_MAX_BYTES`; a broken or MITM'd
    directory can't OOM the reconciler mid-batch. SCIM /Users/{id}
    responses are typically <5 KB; 1 MiB is generous headroom.
  * Distinguishes NOT_FOUND (404) from SUSPENDED (active=False in body)
    from INFRA_ERROR (5xx / network). The `scim_agent` reconciler
    handles NOT_FOUND / SUSPENDED as QUARANTINE, INFRA_ERROR as
    transient (does not mass-quarantine).
"""
from __future__ import annotations

import json as _json
import os
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import quote

import httpx
import structlog

logger = structlog.get_logger(__name__)

ScimStatus = Literal["ACTIVE", "SUSPENDED", "NOT_FOUND"]

# Bytes cap for a single SCIM /Users/{id} response. A broken directory
# streaming an infinite body would otherwise OOM the reconciler; the
# customer's directory is trusted for AUTH but not for AVAILABILITY.
_SCIM_MAX_BYTES = int(os.getenv("SCIM_MAX_RESPONSE_BYTES", str(1 * 1024 * 1024)))


class ScimTransientError(Exception):
    """Raised on 5xx / timeout / network error — caller keeps the
    subject in its current state until the next reconcile pass."""


@dataclass(frozen=True)
class ScimClientConfig:
    base_url: str          # https://<tenant>.scim.example/scim/v2
    bearer_token: str
    timeout_seconds: float = 5.0
    verify_tls: bool = True


class ScimClient:
    """Minimal SCIM 2.0 client. One instance per (tenant, base_url)."""

    def __init__(self, config: ScimClientConfig, http_client: httpx.AsyncClient | None = None) -> None:
        self._cfg = config
        # Optional injection lets tests supply an httpx.MockTransport.
        self._http = http_client

    async def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return httpx.AsyncClient(
            timeout=self._cfg.timeout_seconds,
            verify=self._cfg.verify_tls,
            headers={
                "Authorization": f"Bearer {self._cfg.bearer_token}",
                "Accept": "application/scim+json",
            },
        )

    async def lookup_user(self, scim_ref: str) -> ScimStatus:
        """Resolve a SCIM user reference to ACTIVE / SUSPENDED / NOT_FOUND.

        `scim_ref` is either the fully-qualified `scim://tenant/Users/{id}`
        overlay reference or the raw user id. The overlay is stripped.

        SECURITY: the extracted user id is (a) VALIDATED against a strict
        char class before URL construction and (b) percent-encoded via
        `urllib.parse.quote(safe='')`. Prevents path traversal
        (`../../admin`), query injection (`u_1?admin=1`), and fragment
        smuggling (`u_1#…`).
        """
        user_id = _extract_user_id(scim_ref)
        if not user_id or not _is_safe_scim_id(user_id):
            return "NOT_FOUND"

        # `safe=''` encodes every non-alphanumeric char including `/` and `.`.
        # Combined with the regex allow-list above, this is defense-in-depth.
        url = f"{self._cfg.base_url.rstrip('/')}/Users/{quote(user_id, safe='')}"

        client = await self._client()
        try:
            try:
                # Stream so we can abort at the byte cap without buffering
                # an infinite response body first.
                async with client.stream("GET", url) as resp:
                    if resp.status_code == 404:
                        return "NOT_FOUND"
                    if 500 <= resp.status_code < 600:
                        raise ScimTransientError(f"scim_5xx: {resp.status_code}")
                    if resp.status_code != 200:
                        # 401/403 = auth misconfig, 400 = malformed ref —
                        # surface as transient (operator sees the metric
                        # spike and fixes config) rather than mass-quarantining.
                        raise ScimTransientError(f"scim_unexpected: {resp.status_code}")
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf.extend(chunk)
                        if len(buf) > _SCIM_MAX_BYTES:
                            raise ScimTransientError(
                                f"scim_body_too_large: >{_SCIM_MAX_BYTES}B",
                            )
                    ct = resp.headers.get("content-type", "")
            except httpx.TimeoutException as exc:
                raise ScimTransientError(f"scim_timeout: {exc}") from exc
            except httpx.HTTPError as exc:
                raise ScimTransientError(f"scim_http_error: {type(exc).__name__}") from exc

            if not ct.startswith("application/"):
                body: dict = {}
            else:
                try:
                    body = _json.loads(bytes(buf) or b"{}")
                except _json.JSONDecodeError as exc:
                    raise ScimTransientError(f"scim_bad_json: {exc}") from exc
                if not isinstance(body, dict):
                    # SCIM 2.0 §3.4.2 responses are objects; a bare array
                    # would be a directory bug. Treat as transient.
                    raise ScimTransientError("scim_body_not_object")
            active = body.get("active")
            if active is False:
                return "SUSPENDED"
            return "ACTIVE"
        finally:
            if self._http is None:
                await client.aclose()


# SCIM 2.0 §3.1.1 says id SHALL be a UUID or a URL-safe string. Real
# providers use UUIDs, opaque tokens, or short hashes. We accept
# alnum + a small set of punctuation. Reject `.`, `/`, `?`, `#`, `&`,
# spaces, unicode — anything that could break URL construction or
# cross a path boundary.
_SAFE_SCIM_ID = re.compile(r"^[A-Za-z0-9_\-:]{1,256}$")


def _is_safe_scim_id(user_id: str) -> bool:
    # fullmatch: .match on ^...$ accepts a trailing newline, which would
    # smuggle a control char into the URL construction below.
    return bool(_SAFE_SCIM_ID.fullmatch(user_id))


def _extract_user_id(scim_ref: str) -> str:
    """Overlay refs look like `scim://tenant/Users/user-id`. Strip.
    Raw ids pass through.

    NOTE: Extraction is separate from VALIDATION. Callers MUST run the
    result through `_is_safe_scim_id` before using it in a URL. This
    separation makes the security boundary explicit — extraction is
    string manipulation, validation is the guard.
    """
    if not scim_ref:
        return ""
    if "/Users/" in scim_ref:
        return scim_ref.rsplit("/Users/", 1)[-1]
    if scim_ref.startswith("scim://"):
        return scim_ref.rsplit("/", 1)[-1]
    return scim_ref
