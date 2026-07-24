"""Gateway proxy for the ATF §6 Execution Witness service.

Same shape as the other proxy routers (compliance.py, autonomy.py) —
the gateway forwards `/witness/*` to the standalone `witness` container
declared in docker-compose.yml.

Kept narrow: `verdict`, `observations`, `heartbeat`, `health`,
`public-key`. The verdict engine + attestation signer live in the
witness service; nothing here understands the payload.

Path-param safety: `witness_id` is validated against a strict char
class BEFORE URL construction AND percent-encoded. Same
defense-in-depth pattern as `sdk/common/scim_client._is_safe_scim_id`.
A witness id like `../../admin` (path traversal), `x?admin=1` (query
smuggling), or `x#frag` (fragment smuggling) is refused as 400
BEFORE any downstream HTTP call.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import structlog
from fastapi import APIRouter, HTTPException, Request

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["witness"])

# SPIFFE IDs are like `spiffe://<td>/<path>` — colons, slashes, dots.
# But path-params come in DECODED, and we're about to interpolate the
# value into a URL. We allow: alnum, `_`, `-`, `:`, `/`, `.`. That
# covers SPIFFE IDs + short alphanumeric witness names. We DISALLOW:
# `?`, `#`, `&`, whitespace, `..` segment, unicode.
_SAFE_WITNESS_ID = re.compile(r"^[A-Za-z0-9_\-:/.]{1,256}$")


def _is_safe_witness_id(wid: str) -> bool:
    # fullmatch: plain .match on ^...$ accepts a trailing newline.
    if not _SAFE_WITNESS_ID.fullmatch(wid):
        return False
    if ".." in wid:
        return False
    return True


def _base() -> str:
    return settings.WITNESS_SERVICE_URL.rstrip("/")


@router.post("/witness/observations")
async def post_observation(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/witness/observations",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/witness/heartbeat/{witness_id:path}")
async def post_heartbeat(witness_id: str, request: Request) -> Any:
    if not _is_safe_witness_id(witness_id):
        raise HTTPException(status_code=400, detail="invalid witness_id")
    resp = await request.app.state.client.post(
        # Percent-encode even after the regex passes — defense in depth
        # against any codepath that reaches this line with a partially-
        # sanitized value. `safe='/:.'` preserves SPIFFE-URI structure
        # without exposing `?` `#` `&`.
        f"{_base()}/witness/heartbeat/{quote(witness_id, safe='/:.')}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/witness/verdict")
async def post_verdict(request: Request) -> Any:
    body = await request.json()
    resp = await request.app.state.client.post(
        f"{_base()}/witness/verdict",
        json=body, headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/witness/health")
async def get_health(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/witness/health",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/witness/public-key")
async def get_public_key(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/witness/public-key",
        headers=internal_headers(request),
    )
    return passthrough(resp)
