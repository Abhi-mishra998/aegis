#!/usr/bin/env python3
"""Relay events from the `acp:billing_alerts` Redis stream to Slack.

Sprints 3.2 + 3.5 push billing-related warning payloads onto a single
Redis stream:

    {
      "kind": "monthly_quota_warning" | "inference_cost_warning",
      "tenant_id" | "scope" + "key",
      "monthly_used" | "used_usd",
      "monthly_cap"  | "cap_usd",
      "percent",
      "ts"
    }

This script reads them via a durable consumer group and POSTs a Slack-
formatted message to `ACP_SLACK_WEBHOOK`. It is intentionally tiny —
running as a sidecar in docker-compose or under systemd is enough; no
dedicated worker service is needed.

Idempotent across restarts: the Redis consumer group remembers what's
been ack'd, so a crash + restart resumes from the next unacked entry.

Required env:

    REDIS_URL              redis://...:6379/0
    ACP_SLACK_WEBHOOK      https://hooks.slack.com/services/...

Optional:

    BILLING_ALERTS_STREAM  default "acp:billing_alerts"
    BILLING_ALERTS_GROUP   default "acp-slack-relay"
    BILLING_ALERTS_NAME    default "$HOSTNAME"

Run:

    REDIS_URL=redis://localhost:6379/0 \
    ACP_SLACK_WEBHOOK=https://hooks.slack.com/services/... \
        python scripts/ops/billing_alerts_relay.py

Stop with Ctrl-C. The consumer group state persists in Redis so
restarting resumes cleanly.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from typing import Any

try:
    import httpx  # type: ignore
    from redis.asyncio import Redis  # type: ignore
except ImportError as exc:  # pragma: no cover
    print(f"missing dep: {exc}; install httpx + redis", file=sys.stderr)
    sys.exit(2)


STREAM = os.environ.get("BILLING_ALERTS_STREAM", "acp:billing_alerts")
GROUP  = os.environ.get("BILLING_ALERTS_GROUP",  "acp-slack-relay")
NAME   = os.environ.get("BILLING_ALERTS_NAME",   socket.gethostname() or "relay")
BLOCK_MS = 5000


def _format_slack(payload: dict[str, Any]) -> dict[str, Any]:
    """Render one billing-alert payload as a Slack `chat.postMessage` body.

    The two known event kinds get tailored headers; unknown kinds get a
    generic envelope so a future producer can ship without code edits
    here."""
    kind = payload.get("kind", "billing_alert")
    if kind == "monthly_quota_warning":
        header = f":warning: Monthly request cap 80% — tenant `{payload.get('tenant_id')}`"
        fields = [
            f"*Used:* {payload.get('monthly_used'):,} / {payload.get('monthly_cap'):,}",
            f"*Percent:* {payload.get('percent')}%",
            f"*Resets:* {payload.get('monthly_resets_at')}",
        ]
    elif kind == "inference_cost_warning":
        header = (f":dollar: Inference cost 80% — "
                  f"`{payload.get('scope')}={payload.get('key')}`")
        fields = [
            f"*Used:* ${payload.get('used_usd')}",
            f"*Cap:* ${payload.get('cap_usd')}",
            f"*Percent:* {payload.get('percent')}%",
            f"*Resets:* {payload.get('resets_at')}",
        ]
    else:
        header = f":bell: ACP billing alert ({kind})"
        fields = [f"```{json.dumps(payload, indent=2, sort_keys=True)}```"]

    return {
        "text": header,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": "*" + header + "*"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(fields)}},
        ],
    }


async def _ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def _post_slack(client: httpx.AsyncClient, webhook: str, body: dict) -> bool:
    try:
        r = await client.post(webhook, json=body, timeout=5.0)
        return r.status_code < 400
    except Exception as exc:
        print(f"[relay] slack post failed: {exc}", file=sys.stderr)
        return False


async def run() -> int:
    redis_url = os.environ.get("REDIS_URL")
    webhook   = os.environ.get("ACP_SLACK_WEBHOOK")
    if not redis_url:
        print("ERROR: REDIS_URL required", file=sys.stderr)
        return 2
    if not webhook:
        print("ERROR: ACP_SLACK_WEBHOOK required", file=sys.stderr)
        return 2

    redis = Redis.from_url(redis_url, decode_responses=False)
    await _ensure_group(redis)
    print(f"[relay] stream={STREAM} group={GROUP} consumer={NAME}", file=sys.stderr)

    async with httpx.AsyncClient() as client:
        while True:
            try:
                messages = await redis.xreadgroup(
                    groupname=GROUP, consumername=NAME,
                    streams={STREAM: ">"}, count=10, block=BLOCK_MS,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[relay] xreadgroup error: {exc}", file=sys.stderr)
                await asyncio.sleep(2)
                continue
            if not messages:
                continue
            for _, batch in messages:
                for msg_id, fields in batch:
                    raw = fields.get(b"data") or fields.get("data") or b"{}"
                    if isinstance(raw, (bytes, bytearray)):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        payload = {"kind": "billing_alert", "raw": raw[:300]}
                    body = _format_slack(payload)
                    ok = await _post_slack(client, webhook, body)
                    if ok:
                        await redis.xack(STREAM, GROUP, msg_id)
    await redis.aclose()
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
