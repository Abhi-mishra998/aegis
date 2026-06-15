"""Sprint 6 — HTTP webhook fan-out for on-call paging.

Generic-HTTP POST with bounded retries. Compatible with PagerDuty's V2
events API, Slack incoming webhooks, Opsgenie, Microsoft Teams — any
service that accepts a JSON body on a single URL.

We deliberately don't auto-detect the destination's quirks. The
operator configures `RemediationPolicy.webhook_url` to whichever flavor
they want; if PagerDuty wants `{routing_key, event_action, payload}`,
that's their wrapper's job, not ours. Sprint 6 ships the carrier.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any


# Exponential backoff schedule between attempts (seconds). 4-attempt total
# pad keeps remediation responsive — a slow webhook shouldn't keep the
# storyline ledger in pending state for too long.
_BACKOFF = (0.0, 0.5, 1.0, 2.0)


async def post_webhook(
    httpx_client: Any,
    url: str,
    payload: dict[str, Any],
    *,
    retries: int = 3,
    timeout_s: float = 5.0,
) -> tuple[bool, str]:
    """POST `payload` to `url`. Return (ok, result_message).

    `retries` is the number of *additional* attempts after the first.
    Total wall-clock budget = sum(_BACKOFF[:retries+1]) + (retries+1) ×
    `timeout_s` — bounded at ~22 s with defaults, which is acceptable
    for a fire-and-forget executor task.

    Any 2xx → success. 5xx + connect timeout → retry. 4xx → fail
    immediately (the webhook is misconfigured; retrying won't help).
    """
    if not url:
        return False, "no webhook_url configured"
    try:
        body = json.dumps(payload)
    except (TypeError, ValueError) as exc:
        return False, f"payload not JSON-serialisable: {exc}"

    headers = {"Content-Type": "application/json"}
    attempts = retries + 1
    last_err = "no attempts made"
    for i in range(attempts):
        if i > 0 and i < len(_BACKOFF):
            await asyncio.sleep(_BACKOFF[i])
        try:
            resp = await httpx_client.post(url, content=body, headers=headers, timeout=timeout_s)
        except Exception as exc:
            last_err = f"transport error: {exc}"
            continue
        code = getattr(resp, "status_code", 0)
        if 200 <= code < 300:
            return True, f"ok status={code}"
        if 400 <= code < 500:
            # Client error — operator misconfiguration; retrying is
            # pointless and could trigger rate limits on the destination.
            return False, f"client error status={code}"
        last_err = f"server error status={code}"
    return False, last_err
