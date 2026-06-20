"""Sprint EI-9 (2026-06-20) — Cloudflare Turnstile bot-defence verifier.

The brutal review flagged "CAPTCHA: None — searched the entire frontend"
as a hole in the demo-abuse story; WAF gives us 2000/5min per source IP
but a corporate NAT hides 100 bots behind one IP. Turnstile adds a real
proof-of-human in front of the spawn flow.

Wire:
  POST /demo/spawn-workspace  (services/gateway/routers/demo.py)
    │
    ├─ extract cf-turnstile-response from JSON body
    ├─ POST it + the remote IP to challenges.cloudflare.com/siteverify
    └─ on `success: false` or no token → 403

Local-dev fallback: if TURNSTILE_SECRET_KEY is unset, ``verify`` returns
True immediately. This keeps `docker compose up` end-to-end usable
without a Cloudflare account.

Reference: <https://developers.cloudflare.com/turnstile/get-started/server-side-validation/>
"""
from __future__ import annotations

import structlog
from fastapi import Request

from sdk.common.config import settings

logger = structlog.get_logger(__name__)


async def verify(
    request: Request,
    *,
    token: str | None,
    source_ip: str | None = None,
) -> tuple[bool, str]:
    """Validate a Turnstile token against the siteverify endpoint.

    Returns (allowed, reason). When TURNSTILE_SECRET_KEY is empty (local
    dev) returns (True, "dev_bypass"). When set:
      - missing/empty token → (False, "missing_token")
      - siteverify success=true → (True, "verified")
      - siteverify success=false → (False, "<error-code>")
      - network failure → (False, "verify_unreachable")  ← fail-closed

    Never raises.
    """
    secret = (settings.TURNSTILE_SECRET_KEY or "").strip()
    if not secret:
        return True, "dev_bypass"

    if not token:
        logger.warning("turnstile_missing_token", source_ip=source_ip or "unknown")
        return False, "missing_token"

    payload = {"secret": secret, "response": token}
    if source_ip and source_ip != "unknown":
        payload["remoteip"] = source_ip

    # Use the request's shared httpx client (already pool-tuned for the
    # gateway). Falls back to a fresh client if app.state.client isn't set,
    # which only happens in tests.
    client = getattr(request.app.state, "client", None)
    try:
        if client is not None:
            r = await client.post(settings.TURNSTILE_VERIFY_URL, data=payload, timeout=5.0)
        else:
            import httpx  # noqa: PLC0415
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post(settings.TURNSTILE_VERIFY_URL, data=payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("turnstile_verify_unreachable", error=str(exc),
                       source_ip=source_ip or "unknown")
        # Fail-closed: bot-defence is the whole point of the check.
        return False, "verify_unreachable"

    if r.status_code != 200:
        logger.warning("turnstile_verify_http_error", http=r.status_code,
                       source_ip=source_ip or "unknown")
        return False, f"http_{r.status_code}"

    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        logger.warning("turnstile_verify_bad_json", body=r.text[:120])
        return False, "bad_response"

    if body.get("success") is True:
        return True, "verified"

    # Cloudflare returns ["error-codes": ["timeout-or-duplicate", ...]]
    # on failure. Surface the first one for logs + telemetry.
    codes = body.get("error-codes") or []
    reason = codes[0] if codes else "rejected"
    logger.warning("turnstile_rejected", reason=reason,
                   source_ip=source_ip or "unknown")
    return False, str(reason)
