"""
Real webhook execution — fires Slack, PagerDuty, Jira, ServiceNow, and
generic webhooks. Also dispatches enforcement actions (KILL_AGENT,
ISOLATE_AGENT, BLOCK_TOOL, THROTTLE, REVOKE_KEY) to the registry and
api services via internal HTTP.

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

Sprint EI-6 (ServiceNow ITSM integration): same per-tenant pattern. The
ServiceNow Table API takes Basic auth — username + password (or service
account password). See fire_servicenow().
"""
from __future__ import annotations

import base64
import json as _json
import os
from typing import Any

import httpx
import structlog

from sdk.common.auth import mesh_headers
from sdk.common.outbound_url_allowlist import OutboundUrlBlocked, validate_outbound_url

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

# N16 (2026-06-21) — every outbound httpx.AsyncClient in this module is
# constructed with ``follow_redirects=False``. The SSRF guard
# (``validate_outbound_url``) only validates the INITIAL URL. An attacker
# who controls a registered webhook host can return a 301 to
# ``http://127.0.0.1:8181`` (OPA admin) or ``http://169.254.169.254/...``
# (cloud-metadata) and exfiltrate IAM creds / pivot through internal
# admin surfaces. Refusing redirects forces the destination owner to host
# the real endpoint directly, where it stays inside the SSRF allow-list.
# Same rule applies to the internal-call _do_* functions: redirects from
# the registry/api are never expected; if one shows up we want to surface it
# rather than chase it.
_FOLLOW_REDIRECTS = False

_REGISTRY_URL     = os.environ.get("REGISTRY_SERVICE_URL", "http://registry:8001")
_API_URL          = os.environ.get("API_SERVICE_URL", "http://api:8005")
_INTERNAL_SECRET  = os.environ["INTERNAL_SECRET"]  # fail-fast: no placeholder default


# S9 (audit P1-9): SSRF validation delegated to the canonical shared
# implementation in sdk.common.outbound_url_allowlist. The autonomy
# copy used to accept http as well as https because operators may
# self-host Jira / ServiceNow behind a TLS-terminating load balancer
# that presents http internally — that intention is preserved here.
_ALLOWED_WEBHOOK_SCHEMES: tuple[str, ...] = ("http", "https")

# Q34 — body-size cap for outbound webhook POSTs where we parse the
# response as JSON. Real Jira / ServiceNow success responses are
# ~1-10 KB; anything past 1 MiB is either a broken vendor or a hostile
# response streaming to OOM the worker. Env-tunable so an unusual
# response can be admitted per deployment. Same class of defense as
# Q17 (OIDC), Q21 (SCIM), Q30 (threatintel feeds).
_WEBHOOK_MAX_RESP_BYTES = int(
    os.getenv("WEBHOOK_MAX_RESP_BYTES", str(1 * 1024 * 1024)),
)


class _WebhookResponseTooLarge(Exception):
    """Downstream returned a body larger than ``_WEBHOOK_MAX_RESP_BYTES``.
    Caller surfaces as a normal downstream-error result, not a stack trace."""


async def _post_capped(
    url: str, *, json_body: Any, headers: dict[str, str],
    timeout: float = WEBHOOK_TIMEOUT,
) -> tuple[int, bytes]:
    """POST with a streamed body-size cap on the response. Returns
    ``(status_code, body_bytes)``. Aborts with
    ``_WebhookResponseTooLarge`` at the ceiling — a hostile or broken
    downstream cannot OOM the worker.

    Test doubles that don't implement ``.stream()`` fall back to
    ``.post()`` + post-hoc len check (imperfect — body already in RAM
    at that point — but real httpx.AsyncClient always uses the
    streaming path). Same fallback pattern as threatintel providers.
    """
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=_FOLLOW_REDIRECTS,
    ) as c:
        stream_fn = getattr(c, "stream", None)
        if stream_fn is not None:
            async with stream_fn(
                "POST", url, json=json_body, headers=headers,
            ) as resp:
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > _WEBHOOK_MAX_RESP_BYTES:
                    raise _WebhookResponseTooLarge(
                        f"downstream declared {declared} > cap {_WEBHOOK_MAX_RESP_BYTES}",
                    )
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > _WEBHOOK_MAX_RESP_BYTES:
                        raise _WebhookResponseTooLarge(
                            f"downstream streamed > cap {_WEBHOOK_MAX_RESP_BYTES}",
                        )
                return resp.status_code, bytes(buf)
        # Fallback for tests that pass an AsyncClient without .stream().
        r = await c.post(url, json=json_body, headers=headers)
        body_text = getattr(r, "text", "") or ""
        body_bytes = body_text.encode("utf-8", errors="ignore")
        if len(body_bytes) > _WEBHOOK_MAX_RESP_BYTES:
            raise _WebhookResponseTooLarge(
                f"downstream body {len(body_bytes)}B > cap {_WEBHOOK_MAX_RESP_BYTES}",
            )
        return r.status_code, body_bytes


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
        validate_outbound_url(url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
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
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=_FOLLOW_REDIRECTS) as c:
            r = await c.post(url, json={"blocks": blocks, "text": message})
        status = "sent" if r.status_code == 200 else "error"
        logger.info("slack_alert_fired", status=status, http_status=r.status_code)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:
        logger.warning("slack_alert_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def fire_teams(message: str, webhook_url: str, context: dict | None = None) -> dict:
    """Post an Adaptive Card to a Microsoft Teams channel via
    incoming-webhook URL. Same SSRF guard + redirect rules as Slack.

    Returns a result dict with ``status`` ∈ {"posted","skipped","error"}.
    Never raises.
    """
    if not webhook_url:
        logger.info("teams_alert_skipped", reason="no_webhook_url_configured")
        return {"status": "skipped", "reason": "no Teams webhook URL configured"}
    try:
        validate_outbound_url(webhook_url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.warning("teams_alert_blocked_ssrf", url=webhook_url, reason=str(exc))
        return {"status": "error", "reason": f"webhook url blocked: {exc}"}

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "body": [
                    {"type": "TextBlock", "size": "Large", "weight": "Bolder",
                     "text": "Aegis alert"},
                    {"type": "TextBlock", "wrap": True, "text": message},
                ],
            },
        }],
    }
    if context:
        payload["attachments"][0]["content"]["body"].append(
            {"type": "FactSet", "facts": [
                {"title": k, "value": str(v)} for k, v in list(context.items())[:10]
            ]}
        )
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=_FOLLOW_REDIRECTS) as c:
            r = await c.post(webhook_url, json=payload)
        status = "posted" if r.status_code in (200, 202) else "error"
        logger.info("teams_alert_fired", status=status, http_status=r.status_code)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("teams_alert_failed", error=str(exc))
        return {"status": "error", "reason": str(exc)}


async def fire_webhook(url: str, body: dict, context: dict | None = None) -> dict:
    """Post an arbitrary canonical-webhook JSON body to a customer-
    configured URL. Used for tenants who wire their own ITSM adapter.

    Same SSRF guard, same redirect discipline. Body is caller-supplied.
    """
    if not url:
        logger.info("webhook_alert_skipped", reason="no_url")
        return {"status": "skipped", "reason": "no webhook URL configured"}
    try:
        validate_outbound_url(url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.warning("webhook_blocked_ssrf", url=url, reason=str(exc))
        return {"status": "error", "reason": f"webhook url blocked: {exc}"}
    payload = dict(body)
    if context:
        payload.setdefault("context", context)
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=_FOLLOW_REDIRECTS) as c:
            r = await c.post(url, json=payload)
        status = "posted" if 200 <= r.status_code < 300 else "error"
        logger.info("webhook_fired", status=status, http_status=r.status_code)
        return {"status": status, "http_status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook_failed", error=str(exc))
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
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        validate_outbound_url(base_url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.warning("jira_blocked_ssrf", base_url=base_url, reason=str(exc))
        return {"status": "error", "reason": f"jira base_url blocked: {exc}"}

    # ADF body: paragraph wrapping the supplied text, or raw ADF if caller
    # passes a JSON string (begins with '{').
    desc_text = description or summary
    if desc_text.lstrip().startswith("{"):
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
        status_code, body_bytes = await _post_capped(
            url, json_body={"fields": fields}, headers=headers,
        )
        if status_code == 201:
            try:
                body = _json.loads(body_bytes) if body_bytes else {}
            except _json.JSONDecodeError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            issue_key = body.get("key", "")
            issue_id  = body.get("id", "")
            logger.info(
                "jira_issue_created",
                issue_key=issue_key, project=project_key, http=status_code,
            )
            return {
                "status":     "created",
                "issue_key":  issue_key,
                "issue_id":   issue_id,
                "issue_url":  f"{base_url.rstrip('/')}/browse/{issue_key}" if issue_key else "",
                "http_status": status_code,
            }
        body_snippet = body_bytes[:200].decode("utf-8", errors="replace")
        logger.warning(
            "jira_issue_create_failed",
            http=status_code, body=body_snippet, project=project_key,
        )
        return {
            "status": "error",
            "http_status": status_code,
            "reason": body_snippet if body_snippet else f"HTTP {status_code}",
        }
    except _WebhookResponseTooLarge as exc:
        logger.warning("jira_response_too_large", error=str(exc))
        return {"status": "error", "reason": "jira response exceeded size cap"}
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


async def fire_servicenow(
    short_description: str,
    *,
    instance_url: str,
    username: str,
    password: str,
    description: str | None = None,
    urgency: int = 2,
    impact: int = 2,
    category: str | None = None,
    assignment_group: str | None = None,
    correlation_id: str | None = None,
    context: dict | None = None,
) -> dict:
    """Create a ServiceNow Incident via the Table API.

    Returns a result dict with ``status`` ∈ {``"created"``, ``"skipped"``,
    ``"error"``}. On success the dict carries the SNOW ``sys_id`` and
    ``number`` (e.g. ``INC0010001``) for round-trip linking and an
    ``incident_url`` the operator can click in Slack. Never raises.

    Auth is HTTP Basic with the dedicated service-account password. For
    SaaS ServiceNow instances enforce strong-password rotation via SNOW's
    own user-management — Aegis does not refresh the credential.

    Urgency and impact follow SNOW's 1-2-3 scale (1=High, 2=Medium, 3=Low);
    values are clamped to that range. SNOW computes ``priority`` from
    urgency × impact on its side — no need to send it.

    Body shape (table API):
      POST <instance>/api/now/table/incident
      {
        "short_description": "...",
        "description":       "...",
        "urgency": "1|2|3",
        "impact":  "1|2|3",
        "category":          "<optional>",
        "assignment_group":  "<optional sys_id>",
        "correlation_id":    "<optional dedup key>"
      }

    Response shape on success (201):
      {"result": {"sys_id": "<32-char hex>",
                  "number": "INC0010001", ...}}
    """
    if not (instance_url and username and password):
        logger.info("snow_create_skipped", reason="missing_config")
        return {"status": "skipped", "reason": "ServiceNow config incomplete"}

    try:
        validate_outbound_url(instance_url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.warning("snow_blocked_ssrf", instance_url=instance_url, reason=str(exc))
        return {"status": "error", "reason": f"servicenow instance_url blocked: {exc}"}

    # Clamp urgency/impact to 1-3 — SNOW rejects anything else.
    # Q33 — the prior bare int() raised ValueError on non-numeric input
    # (e.g. urgency="high") and was caught by the outer except Exception,
    # masking the parse failure as "ServiceNow unavailable". Distinguish
    # bad-input (client error) from network unavailability.
    try:
        urgency = max(1, min(3, int(urgency or 2)))
        impact  = max(1, min(3, int(impact  or 2)))
    except (ValueError, TypeError) as exc:
        return {
            "status": "error",
            "reason": f"urgency and impact must be integers 1-3: {exc}",
        }

    desc_text = description or short_description
    if context:
        ctx_lines = "\n".join(f"  {k}: {v}" for k, v in list(context.items())[:10])
        desc_text = f"{desc_text}\n\n--- Aegis context ---\n{ctx_lines}"

    body: dict = {
        "short_description": short_description[:160],   # SNOW field cap
        "description":       desc_text,
        "urgency":           str(urgency),
        "impact":            str(impact),
    }
    if category:
        body["category"] = category
    if assignment_group:
        body["assignment_group"] = assignment_group
    if correlation_id:
        # SNOW de-dupes on correlation_id when it's set — same id means
        # SNOW won't open a second ticket, it returns the existing one.
        body["correlation_id"] = correlation_id

    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    url = f"{instance_url.rstrip('/')}/api/now/table/incident"

    try:
        status_code, body_bytes = await _post_capped(
            url, json_body=body, headers=headers,
        )
        if status_code == 201:
            try:
                parsed = _json.loads(body_bytes) if body_bytes else {}
            except _json.JSONDecodeError:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            data = parsed.get("result") or {}
            if not isinstance(data, dict):
                data = {}
            sys_id = data.get("sys_id", "")
            number = data.get("number", "")
            logger.info(
                "snow_incident_created",
                number=number, sys_id=sys_id[:8], http=status_code,
            )
            return {
                "status":       "created",
                "sys_id":       sys_id,
                "number":       number,
                "incident_url": (
                    f"{instance_url.rstrip('/')}/nav_to.do?uri=incident.do?sys_id={sys_id}"
                    if sys_id else ""
                ),
                "http_status":  status_code,
            }
        # N26 (2026-06-21): full SNOW response body stays in the internal log,
        # but the caller-visible `reason` is a generic class so we don't echo
        # SNOW's 401 (which can reflect the username + password-length hints)
        # into the operator's HTTP response.
        logger.warning(
            "snow_incident_create_failed",
            http=status_code,
            body=body_bytes[:500].decode("utf-8", errors="replace"),
        )
        return {
            "status":      "error",
            "http_status": status_code,
            "reason":      _safe_snow_error(status_code),
        }
    except _WebhookResponseTooLarge as exc:
        logger.warning("snow_response_too_large", error=str(exc))
        return {"status": "error", "reason": "ServiceNow response exceeded size cap"}
    except Exception as exc:
        # N26: same scrub on the network-level exception path. The full
        # exception string (which could include the URL + credentials of a
        # misconfigured proxy) stays in the log; the caller gets a generic
        # "ServiceNow unavailable".
        logger.warning("snow_incident_create_exception", error=str(exc)[:500])
        return {"status": "error", "reason": "ServiceNow unavailable"}


def _safe_snow_error(status: int) -> str:
    """N26: map a SNOW HTTP status to a caller-visible class.

    The mapping is intentionally coarse: any 4xx maps to one of two
    sanitised strings ("ServiceNow auth failed" vs "ServiceNow rejected
    request"), any 5xx maps to "ServiceNow unavailable". The exact
    upstream body is in the structured log under the request id so an
    operator can correlate, but it never lands in the HTTP response body
    where a curious tenant admin could read it (or where a JS client
    could pipe it to a third party).
    """
    if status in (401, 403):
        return "ServiceNow auth failed"
    if 400 <= status < 500:
        return "ServiceNow rejected request"
    if 500 <= status < 600:
        return "ServiceNow unavailable"
    return f"ServiceNow returned unexpected status {status}"


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
        validate_outbound_url(url, allowed_schemes=_ALLOWED_WEBHOOK_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.warning("generic_webhook_blocked_ssrf", url=url, reason=str(exc))
        return {"status": "error", "reason": f"webhook url blocked: {exc}"}

    hdrs = {"Content-Type": "application/json", **(headers or {})}
    try:
        async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        **mesh_headers("autonomy"),
        **({"X-Tenant-ID": tenant_id} if tenant_id else {}),
    }


async def _do_kill_agent(agent_id: str, tenant_id: str = "") -> dict:
    """Suspend agent in the registry and write a kill-switch key via the gateway."""
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=_FOLLOW_REDIRECTS) as c:
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
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=_FOLLOW_REDIRECTS) as c:
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

    elif action_type == "CREATE_SNOW_INCIDENT":
        return await fire_servicenow(
            short_description=(
                params.get("short_description")
                or params.get("summary")
                or ctx.get("summary")
                or "Aegis incident"
            ),
            instance_url=params.get("instance_url", ""),
            username=params.get("username", ""),
            password=params.get("password", ""),
            description=params.get("description") or ctx.get("description"),
            urgency=params.get("urgency", 2),
            impact=params.get("impact", 2),
            category=params.get("category"),
            assignment_group=params.get("assignment_group"),
            correlation_id=params.get("correlation_id") or ctx.get("incident_id"),
            context=ctx,
        )

    else:
        logger.info("playbook_action_unknown", action_type=action_type)
        return {"status": "skipped", "action_type": action_type, "reason": "unknown action type"}
