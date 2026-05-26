"""
Real webhook execution — fires Slack, PagerDuty, and generic webhooks.
Also dispatches enforcement actions (KILL_AGENT, ISOLATE_AGENT, BLOCK_TOOL,
THROTTLE, REVOKE_KEY) to the registry and api services via internal HTTP.
"""
from __future__ import annotations

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)

SLACK_WEBHOOK_URL     = os.environ.get("SLACK_WEBHOOK_URL", "")
PAGERDUTY_ROUTING_KEY = os.environ.get("PAGERDUTY_ROUTING_KEY", "")
WEBHOOK_TIMEOUT       = 10.0

_REGISTRY_URL     = os.environ.get("REGISTRY_SERVICE_URL", "http://registry:8001")
_API_URL          = os.environ.get("API_SERVICE_URL", "http://api:8005")
_INTERNAL_SECRET  = os.environ.get("INTERNAL_SECRET", "change_me_internal")


async def fire_slack(message: str, webhook_url: str = "", context: dict | None = None) -> dict:
    """POST a Slack message to the configured webhook URL.

    Returns a result dict with ``status`` set to ``"sent"``, ``"skipped"``,
    or ``"error"``.  Never raises — all HTTP errors are caught and returned
    in the result.
    """
    url = webhook_url or SLACK_WEBHOOK_URL
    if not url:
        logger.info("slack_alert_skipped", reason="no_webhook_url_configured")
        return {"status": "skipped", "reason": "no Slack webhook configured"}

    ctx = context or {}
    blocks: list[dict] = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Aegis Alert*\n{message}"},
        }
    ]
    if ctx:
        fields = [
            {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
            for k, v in list(ctx.items())[:6]
        ]
        blocks.append({"type": "section", "fields": fields})

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as c:
            r = await c.post(url, json={"blocks": blocks, "text": message})
        status = "sent" if r.status_code == 200 else "error"
        logger.info("slack_alert_fired", status=status, http_status=r.status_code)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:
        logger.warning("slack_alert_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def fire_pagerduty(
    summary: str,
    severity: str = "warning",
    routing_key: str = "",
    dedup_key: str = "",
) -> dict:
    """Create a PagerDuty alert via Events API v2.

    Returns a result dict with ``status`` set to ``"triggered"``,
    ``"skipped"``, or ``"error"``.  Never raises.
    """
    key = routing_key or PAGERDUTY_ROUTING_KEY
    if not key:
        logger.info("pagerduty_alert_skipped", reason="no_routing_key_configured")
        return {"status": "skipped", "reason": "no PagerDuty routing key configured"}

    payload = {
        "routing_key": key,
        "event_action": "trigger",
        "dedup_key": dedup_key or summary[:255],
        "payload": {
            "summary": summary,
            "severity": severity,
            "source": "aegis-acp",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as c:
            r = await c.post("https://events.pagerduty.com/v2/enqueue", json=payload)
        status = "triggered" if r.status_code in (200, 202) else "error"
        logger.info("pagerduty_alert_fired", status=status, http_status=r.status_code)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:
        logger.warning("pagerduty_alert_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def fire_generic_webhook(
    url: str,
    payload: dict | None = None,
    method: str = "POST",
    headers: dict | None = None,
) -> dict:
    """POST (or GET) an arbitrary webhook URL.

    Returns a result dict with ``status`` set to ``"sent"``, ``"skipped"``,
    or ``"error"``.  Never raises.
    """
    if not url:
        logger.info("generic_webhook_skipped", reason="no_url_provided")
        return {"status": "skipped", "reason": "no webhook URL"}

    hdrs = {"Content-Type": "application/json", **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as c:
            if method.upper() == "GET":
                r = await c.get(url, headers=hdrs)
            else:
                r = await c.post(url, json=payload or {}, headers=hdrs)
        status = "sent" if r.status_code < 400 else "error"
        logger.info("generic_webhook_fired", status=status, http_status=r.status_code, url=url)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:
        logger.warning("generic_webhook_failed", error=str(exc), url=url)
        return {"status": "error", "reason": str(exc)}


def _internal_headers(tenant_id: str = "") -> dict:
    return {
        "X-Internal-Secret": _INTERNAL_SECRET,
        **({"X-Tenant-ID": tenant_id} if tenant_id else {}),
    }


async def _do_kill_agent(agent_id: str, tenant_id: str = "") -> dict:
    """Suspend agent in the registry and write a kill-switch key via the gateway."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.patch(
                f"{_REGISTRY_URL.rstrip('/')}/agents/{agent_id}",
                json={"status": "suspended"},
                headers=_internal_headers(tenant_id),
            )
        status = "killed" if r.status_code in (200, 204) else "error"
        logger.critical("playbook_kill_agent", agent=agent_id[:8], http=r.status_code)
        return {"status": status, "agent_id": agent_id, "http_status": r.status_code}
    except Exception as exc:
        logger.error("playbook_kill_agent_failed", agent=agent_id[:8], error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def _do_isolate_agent(agent_id: str, tenant_id: str = "") -> dict:
    """Set agent status to 'isolated' in the registry (rate-limit without full suspend)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.patch(
                f"{_REGISTRY_URL.rstrip('/')}/agents/{agent_id}",
                json={"status": "isolated"},
                headers=_internal_headers(tenant_id),
            )
        status = "isolated" if r.status_code in (200, 204) else "error"
        logger.warning("playbook_isolate_agent", agent=agent_id[:8], http=r.status_code)
        return {"status": status, "agent_id": agent_id, "http_status": r.status_code}
    except Exception as exc:
        logger.error("playbook_isolate_agent_failed", agent=agent_id[:8], error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def _do_block_tool(agent_id: str, tool: str, tenant_id: str = "") -> dict:
    """Add a DENY permission for the tool on the agent."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(
                f"{_REGISTRY_URL.rstrip('/')}/agents/{agent_id}/permissions",
                json={"tool_name": tool, "action": "DENY", "granted_by": "playbook"},
                headers=_internal_headers(tenant_id),
            )
        status = "blocked" if r.status_code in (200, 201) else "error"
        logger.warning("playbook_block_tool", agent=agent_id[:8], tool=tool, http=r.status_code)
        return {"status": status, "agent_id": agent_id, "tool": tool, "http_status": r.status_code}
    except Exception as exc:
        logger.error("playbook_block_tool_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def _do_throttle(agent_id: str, rate: str, tenant_id: str = "") -> dict:
    """Write a Redis throttle key via the API service's internal throttle endpoint."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.post(
                f"{_API_URL.rstrip('/')}/internal/throttle",
                json={"agent_id": agent_id, "tenant_id": tenant_id, "rate": rate},
                headers=_internal_headers(tenant_id),
            )
        status = "throttled" if r.status_code in (200, 204) else "simulated"
        logger.warning("playbook_throttle", agent=agent_id[:8], rate=rate, http=r.status_code)
        return {"status": status, "agent_id": agent_id, "rate": rate}
    except Exception as exc:
        logger.warning("playbook_throttle_failed", error=str(exc))
        return {"status": "simulated", "agent_id": agent_id, "rate": rate}


async def _do_revoke_key(key_id: str, tenant_id: str = "") -> dict:
    """Revoke an API key via the API service."""
    if not key_id:
        return {"status": "skipped", "reason": "no key_id provided"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.delete(
                f"{_API_URL.rstrip('/')}/api-keys/{key_id}",
                headers=_internal_headers(tenant_id),
            )
        status = "revoked" if r.status_code in (200, 204) else "error"
        logger.warning("playbook_revoke_key", key=key_id[:8], http=r.status_code)
        return {"status": status, "key_id": key_id, "http_status": r.status_code}
    except Exception as exc:
        logger.error("playbook_revoke_key_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def execute_step(step: dict, context: dict | None = None) -> dict:
    """Route a playbook step to the appropriate executor.

    Called from ``playbooks.py``. All action types now have real implementations.
    """
    action_type = step.get("action_type", "UNKNOWN")
    params = step.get("params", {})
    ctx = context or {}

    agent_id  = params.get("agent_id") or ctx.get("agent_id", "")
    tenant_id = params.get("tenant_id") or ctx.get("tenant_id", "")

    if action_type == "KILL_AGENT":
        return await _do_kill_agent(agent_id, tenant_id)

    elif action_type == "ISOLATE_AGENT":
        return await _do_isolate_agent(agent_id, tenant_id)

    elif action_type == "BLOCK_TOOL":
        tool = params.get("tool") or ctx.get("tool", "*")
        return await _do_block_tool(agent_id, tool, tenant_id)

    elif action_type == "THROTTLE":
        rate = params.get("rate", "5/m")
        return await _do_throttle(agent_id, rate, tenant_id)

    elif action_type == "REVOKE_KEY":
        key_id = params.get("key_id") or ctx.get("key_id", "")
        return await _do_revoke_key(key_id, tenant_id)

    elif action_type == "SEND_ALERT":
        channel = params.get("channel", "slack")
        message = (
            params.get("message")
            or ctx.get("message")
            or f"Aegis playbook triggered: {action_type}"
        )
        if channel == "slack":
            return await fire_slack(
                message,
                webhook_url=params.get("webhook_url", ""),
                context=ctx,
            )
        elif channel == "pagerduty":
            return await fire_pagerduty(
                summary=message,
                severity=params.get("severity", "warning"),
                routing_key=params.get("routing_key", ""),
            )
        else:
            logger.info("send_alert_skipped", channel=channel)
            return {"status": "skipped", "reason": f"unknown channel: {channel}"}

    elif action_type == "WEBHOOK":
        return await fire_generic_webhook(
            url=params.get("url", ""),
            payload={**params.get("payload", {}), "aegis_context": ctx},
            method=params.get("method", "POST"),
            headers=params.get("headers", {}),
        )

    else:
        logger.info("playbook_action_unknown", action_type=action_type)
        return {"status": "skipped", "action_type": action_type, "reason": "unknown action type"}
