"""
Real webhook execution — fires Slack, PagerDuty, Jira, and generic webhooks.
Also dispatches enforcement actions (KILL_AGENT, ISOLATE_AGENT, BLOCK_TOOL,
THROTTLE, REVOKE_KEY) to the registry and api services via internal HTTP.

Sprint 2b (closes audit C17): Slack + PagerDuty credentials can be loaded
from AWS SSM Parameter Store at boot — ``ALERT_CRED_SOURCE=ssm`` selects
the SSM path with ``ALERT_SSM_PREFIX`` (default ``/aegis-alerts``).
Each path stores one SecureString per credential:

    /aegis-alerts/SLACK_WEBHOOK_URL
    /aegis-alerts/PAGERDUTY_ROUTING_KEY

This matches the existing ``/aegis-siem/*`` convention in the account so an
operator only needs to remember one ssm:put-parameter command shape.

Sprint EI-2 (Jira ITSM integration): Jira config is *per-tenant*, persisted
to the identity DB (not SSM/env) so a tenant can self-serve. The executor
accepts the config in the ``params`` dict at call time — see fire_jira().
"""
from __future__ import annotations

import base64
import ipaddress
import os
import socket
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)


def _load_alert_credentials_from_ssm(prefix: str) -> dict[str, str]:
    """Read every parameter under ``{prefix}/`` and return UPPER_SNAKE-keyed
    values. Returns an empty dict on any boto3 error so a misconfigured
    deployment still boots — the env-var fallback below catches it."""
    try:
        import boto3  # noqa: PLC0415
    except ImportError:
        return {}
    out: dict[str, str] = {}
    try:
        ssm = boto3.client("ssm")
        paginator = ssm.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=f"{prefix.rstrip('/')}/", WithDecryption=True):
            for p in page.get("Parameters", []):
                key = p["Name"].split("/")[-1].upper()
                out[key] = p["Value"]
    except Exception as exc:
        logger.warning("alert_credentials_ssm_failed", error=str(exc), prefix=prefix)
        return {}
    return out


def _resolve_alert_credentials() -> dict[str, str]:
    source = (os.environ.get("ALERT_CRED_SOURCE") or "env").strip().lower()
    if source == "ssm":
        prefix = os.environ.get("ALERT_SSM_PREFIX", "/aegis-alerts")
        out = _load_alert_credentials_from_ssm(prefix)
        # Treat the Sprint 2b ``PENDING_*`` placeholders as if the parameter
        # were unset — the operator hasn't filled it in yet.
        return {k: v for k, v in out.items() if v and not v.startswith("PENDING_")}
    return {
        "SLACK_WEBHOOK_URL":     os.environ.get("SLACK_WEBHOOK_URL", ""),
        "PAGERDUTY_ROUTING_KEY": os.environ.get("PAGERDUTY_ROUTING_KEY", ""),
    }


_alert_creds = _resolve_alert_credentials()
SLACK_WEBHOOK_URL     = _alert_creds.get("SLACK_WEBHOOK_URL", "")
PAGERDUTY_ROUTING_KEY = _alert_creds.get("PAGERDUTY_ROUTING_KEY", "")
WEBHOOK_TIMEOUT       = 10.0

_REGISTRY_URL     = os.environ.get("REGISTRY_SERVICE_URL", "http://registry:8001")
_API_URL          = os.environ.get("API_SERVICE_URL", "http://api:8005")
_INTERNAL_SECRET  = os.environ["INTERNAL_SECRET"]  # fail-fast: no placeholder default


class _SSRFBlocked(Exception):
    """Raised when a webhook URL targets a forbidden IP range."""


_FORBIDDEN_HOSTS = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.aws.internal",
})


def _assert_safe_webhook_url(url: str) -> None:
    """Reject URLs that target loopback, RFC1918, link-local, or cloud-metadata IPs.

    Resolves the hostname and validates every returned address. Prevents an
    authenticated user from supplying e.g. http://169.254.169.254/... and
    exfiltrating EC2 IAM credentials through the autonomy webhook surface.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise _SSRFBlocked(f"forbidden scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise _SSRFBlocked("missing host")
    if host in _FORBIDDEN_HOSTS:
        raise _SSRFBlocked(f"forbidden host: {host}")
    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise _SSRFBlocked(f"hostname resolution failed: {exc}") from exc
    for _family, _type, _proto, _canon, sockaddr in addrinfo:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise _SSRFBlocked(f"forbidden IP {ip} for host {host}")


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

    try:
        _assert_safe_webhook_url(url)
    except _SSRFBlocked as exc:
        logger.warning("slack_alert_blocked_ssrf", url=url, reason=str(exc))
        return {"status": "error", "reason": f"webhook url blocked: {exc}"}

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


async def fire_jira(
    summary: str,
    *,
    base_url: str,
    account_email: str,
    api_token: str,
    project_key: str,
    issue_type: str = "Bug",
    description: str | None = None,
    priority: str | None = None,
    labels: list[str] | None = None,
    context: dict | None = None,
) -> dict:
    """Create a Jira Cloud issue via REST API v3.

    Returns a result dict with ``status`` ∈ {``"created"``, ``"skipped"``,
    ``"error"``}. On success the dict carries the Jira ``issue_key`` and
    ``issue_id`` so the caller can store it on the originating incident
    for round-trip linking. Never raises.

    Auth is Basic with base64(email:api_token). The on-prem Server API
    differs from Cloud — this implementation targets Cloud (`/rest/api/3/`).

    Description is sent as Atlassian Document Format (ADF) — a paragraph
    node wrapping the supplied text, which is enough for incident-link
    bodies. Callers wanting richer formatting can pass a fully-formed ADF
    document via the ``description`` parameter as a JSON string starting
    with ``{`` — it will be parsed and passed through verbatim.
    """
    if not (base_url and account_email and api_token and project_key):
        logger.info("jira_create_skipped", reason="missing_config")
        return {"status": "skipped", "reason": "Jira config incomplete"}

    try:
        _assert_safe_webhook_url(base_url)
    except _SSRFBlocked as exc:
        logger.warning("jira_blocked_ssrf", base_url=base_url, reason=str(exc))
        return {"status": "error", "reason": f"jira base_url blocked: {exc}"}

    # ADF body: paragraph wrapping the supplied text, or raw ADF if caller
    # passes a JSON string (begins with '{').
    desc_text = description or summary
    if desc_text.lstrip().startswith("{"):
        import json as _json
        try:
            adf_body = _json.loads(desc_text)
        except Exception:
            adf_body = _adf_paragraph(desc_text)
    else:
        adf_body = _adf_paragraph(desc_text, context=context)

    fields: dict = {
        "project":   {"key": project_key},
        "summary":   summary[:255],
        "issuetype": {"name": issue_type},
        "description": adf_body,
    }
    if priority:
        fields["priority"] = {"name": priority}
    if labels:
        fields["labels"] = [str(s) for s in labels][:20]

    auth = base64.b64encode(f"{account_email}:{api_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{base_url.rstrip('/')}/rest/api/3/issue"

    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT) as c:
            r = await c.post(url, json={"fields": fields}, headers=headers)
        if r.status_code == 201:
            body = r.json()
            issue_key = body.get("key", "")
            issue_id  = body.get("id", "")
            logger.info(
                "jira_issue_created",
                issue_key=issue_key, project=project_key, http=r.status_code,
            )
            return {
                "status":     "created",
                "issue_key":  issue_key,
                "issue_id":   issue_id,
                "issue_url":  f"{base_url.rstrip('/')}/browse/{issue_key}" if issue_key else "",
                "http_status": r.status_code,
            }
        logger.warning(
            "jira_issue_create_failed",
            http=r.status_code, body=r.text[:200], project=project_key,
        )
        return {
            "status": "error",
            "http_status": r.status_code,
            "reason": r.text[:200] if r.text else f"HTTP {r.status_code}",
        }
    except Exception as exc:
        logger.warning("jira_issue_create_exception", error=str(exc))
        return {"status": "error", "reason": str(exc)}


def _adf_paragraph(text: str, *, context: dict | None = None) -> dict:
    """Build a minimal Atlassian Document Format body from plain text.

    A single paragraph node carrying the message, followed by an optional
    bullet list of context key/value pairs. Sufficient for ticket bodies
    posted by Aegis; richer ADF is left to the caller.
    """
    content: list[dict] = [
        {
            "type": "paragraph",
            "content": [{"type": "text", "text": text}],
        }
    ]
    if context:
        items = []
        for k, v in list(context.items())[:10]:
            items.append({
                "type": "listItem",
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"{k}: {v}"}],
                }],
            })
        content.append({"type": "bulletList", "content": items})
    return {"type": "doc", "version": 1, "content": content}


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

    try:
        _assert_safe_webhook_url(url)
    except _SSRFBlocked as exc:
        logger.warning("generic_webhook_blocked_ssrf", url=url, reason=str(exc))
        return {"status": "error", "reason": f"webhook url blocked: {exc}"}

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

    elif action_type == "CREATE_JIRA_ISSUE":
        return await fire_jira(
            summary=params.get("summary") or ctx.get("summary") or "Aegis incident",
            base_url=params.get("base_url", ""),
            account_email=params.get("account_email", ""),
            api_token=params.get("api_token", ""),
            project_key=params.get("project_key", ""),
            issue_type=params.get("issue_type", "Bug"),
            description=params.get("description") or ctx.get("description"),
            priority=params.get("priority"),
            labels=params.get("labels"),
            context=ctx,
        )

    else:
        logger.info("playbook_action_unknown", action_type=action_type)
        return {"status": "skipped", "action_type": action_type, "reason": "unknown action type"}
