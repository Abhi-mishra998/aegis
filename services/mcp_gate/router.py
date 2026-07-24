"""ATF §5.1 MCP proxy router — FastAPI surface.

Accepts JSON-RPC 2.0 requests on `POST /mcp/messages`. For every
`tools/call`:

  1. Extract (tool_name, arguments).
  2. Ask the Aegis Policy service for a decision on this tenant + agent.
  3. If ALLOW: forward the ENTIRE original body to the configured
     downstream MCP server; stream the response back verbatim.
  4. If DENY / ESCALATE / QUARANTINE: return a JSON-RPC error with a
     code the MCP client can distinguish from transport failures.

Non-tools/call methods (initialize, tools/list, ping, etc.) forward
unchanged — they're metadata, not consequential actions.

# ─────────────────────────────────────────────────────────────
# Auth model — the intended caller is the AGENT RUNTIME
# (co-located with the gate in the same pod / host per §5.1). The
# agent doesn't have a mesh JWT — the mesh is for inter-service
# calls. Instead:
#
#   * Agent runtime sends `Authorization: Bearer <MCP_GATE_BEARER_TOKEN>`
#     — a tenant-configured shared secret rotated by ops.
#   * Comparison is `hmac.compare_digest` to prevent timing oracles.
#   * If `MCP_GATE_BEARER_TOKEN` is unset AND environment is `production`,
#     the service refuses to boot (fail-CLOSED at config time).
#   * If unset in dev/test, an ephemeral token is generated + logged so
#     local iteration works without manual setup.
#
# The mesh JWT is minted OUTBOUND by the gate to call the policy service.
# ─────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hmac
import os
import secrets
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from sdk.common.audit_stream import push_audit_event
from sdk.common.auth import mesh_headers
from sdk.common.config import settings
from sdk.common.outbound_url_allowlist import (
    OutboundUrlBlocked,
    validate_outbound_url,
)
from sdk.common.redis import get_redis_client
from services.mcp_gate.proxy import (
    _ERR_INTERNAL,
    _ERR_INVALID_REQUEST,
    GateDecision,
    McpParseError,
    build_gate_denial,
    extract_tool_call,
    is_tools_call,
    json_rpc_error,
    parse_mcp_call,
)

logger = structlog.get_logger(__name__)


def _resolve_bearer_token() -> str:
    """Read the configured bearer token. In production, unset is a
    fail-CLOSED boot error — never generate a random secret in prod
    because the agent runtime that shipped alongside can't discover it.

    In dev/test (ENVIRONMENT != 'production'), generate + log an
    ephemeral token so local iteration works. The token is fresh per
    process; a running agent must be reconfigured on gate restart in
    dev.
    """
    tok = os.getenv("MCP_GATE_BEARER_TOKEN", "")
    if tok:
        return tok
    env = (os.getenv("ENVIRONMENT", "development") or "development").lower()
    if env == "production":
        raise RuntimeError(
            "MCP_GATE_BEARER_TOKEN must be set in production. "
            "Refusing to boot with an ephemeral token — the agent runtime "
            "would have no way to discover it.",
        )
    tok = secrets.token_urlsafe(32)
    logger.warning(
        "mcp_gate_dev_bearer_token_generated",
        length=len(tok), env=env,
        note="dev-only ephemeral token; set MCP_GATE_BEARER_TOKEN for stable runs",
    )
    return tok


_EXPECTED_BEARER = _resolve_bearer_token()


def verify_mcp_bearer(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Constant-time bearer-token check. Refuses missing / wrong-shape /
    wrong-value tokens with a UNIFORM error message + WWW-Authenticate
    header — no oracle for which failure mode fired."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Bearer realm="aegis-mcp-gate"'},
        )
    presented = authorization.split(" ", 1)[1].strip()
    # hmac.compare_digest is the standard constant-time comparison.
    if not hmac.compare_digest(presented, _EXPECTED_BEARER):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Bearer realm="aegis-mcp-gate"'},
        )


router = APIRouter(
    prefix="/mcp",
    tags=["mcp-gate"],
    dependencies=[Depends(verify_mcp_bearer)],
)

# Timeout for the downstream MCP server. MCP tool calls can be
# long-running (browser automation, DB queries) so we allow a
# generous ceiling but never unbounded.
_DOWNSTREAM_TIMEOUT_S = float(os.getenv("MCP_GATE_DOWNSTREAM_TIMEOUT_S", "60.0"))
# ATF §3.2 default-deny egress requirement — the proxy MUST only reach
# the configured downstream. HTTP-scheme allowed for on-cluster
# self-hosted MCP servers; TLS-terminating LBs are the deployment norm.
_ALLOWED_SCHEMES: tuple[str, ...] = ("http", "https")
# DoS ceiling: MCP JSON-RPC requests are small (typically < 32 KB —
# tool name + args). 1 MiB is generous headroom for tool-args that
# ship a chunked file; anything bigger is not a legitimate MCP call.
# Without this, an attacker at the bearer boundary could exhaust the
# gate's memory with one giant request body.
_MAX_BODY_BYTES = int(os.getenv("MCP_GATE_MAX_BODY_BYTES", str(1 * 1024 * 1024)))
# DoS ceiling on the DOWNSTREAM's response — a hostile or
# misconfigured MCP server could stream gigabytes and OOM the gate
# worker before we ever touch the body. 16 MiB covers legitimate
# tool responses (browser screenshots, DB result sets); anything
# larger is either a config error or an attack.
_MAX_RESP_BYTES = int(os.getenv("MCP_GATE_MAX_RESP_BYTES", str(16 * 1024 * 1024)))


def _downstream_url() -> str:
    """Downstream MCP server URL. Blank disables the proxy entirely
    (returns 503 on any request) — no silent forward to a default."""
    return os.getenv("MCP_GATE_DOWNSTREAM_URL", "").rstrip("/")


async def _call_policy_gate(
    tenant_id: str,
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> GateDecision | None:
    """POST to the policy service /policy/evaluate. Returns None if the
    policy service is unreachable — caller decides fallback behavior.
    Fails CLOSED at the router level (§3.2 default-deny on unknown)."""
    policy_url = f"{settings.POLICY_SERVICE_URL.rstrip('/')}/policy/evaluate"
    body = {
        "tenant_id":  tenant_id,
        "agent_id":   agent_id,
        "tool":       tool_name,
        "params":     arguments,
        "risk_score": 0.0,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            resp = await c.post(policy_url, json=body, headers=mesh_headers("mcp_gate"))
    except httpx.HTTPError as exc:
        logger.error("mcp_gate_policy_unreachable", error=str(exc))
        return None

    if resp.status_code != 200:
        logger.warning("mcp_gate_policy_non_200", status=resp.status_code)
        return None

    payload = resp.json().get("data") or {}
    raw_tier = payload.get("tier") or ("allow" if payload.get("allowed") else "deny")

    # Defense-in-depth: any tier the router doesn't recognize must
    # collapse to "deny". A misconfigured/attacker-influenced policy
    # response with tier="Allow" (case) or "allowed" (typo) would
    # otherwise fall through the `!= "allow"` guard in the router —
    # which is fail-CLOSED, but silently. Log the anomaly + explicitly
    # coerce so the audit trail names what happened.
    _VALID: set[str] = {"allow", "monitor", "escalate", "deny", "quarantine"}
    if raw_tier not in _VALID:
        logger.warning(
            "mcp_gate_policy_returned_unknown_tier",
            raw_tier=str(raw_tier)[:64],
            policy_id=str(payload.get("policy_id"))[:64],
        )
        tier = "deny"
    else:
        tier = raw_tier

    return GateDecision(
        tier=tier,     # type: ignore[arg-type]
        reason=str(payload.get("reason") or "policy_decision"),
        policy_id=str(payload.get("policy_id") or ""),
        findings=list(payload.get("findings") or []),
    )


class DownstreamResponseTooLarge(Exception):
    """Downstream returned a body larger than `_MAX_RESP_BYTES`. Caller
    surfaces this as a 502 to the agent runtime — same shape as any
    other downstream transport failure."""


async def _forward_to_downstream(body: bytes, headers: dict[str, str]) -> httpx.Response:
    """POST the original JSON-RPC body to the downstream MCP server.
    SSRF-guarded even though the URL is operator-configured: prevents
    a compromised env var from targeting internal admin surfaces.

    Auth injection: if the operator has configured
    `MCP_GATE_DOWNSTREAM_BEARER_TOKEN`, the gate adds it as
    `Authorization: Bearer <token>` on outbound calls. This is a
    SEPARATE credential from the inbound `MCP_GATE_BEARER_TOKEN`
    (which authenticates the agent runtime TO the gate). The agent's
    inbound token is NEVER forwarded — the gate is a fresh trust
    boundary from the downstream's perspective.

    Response body is capped at `_MAX_RESP_BYTES` via a streamed
    read + running byte counter. A hostile/misconfigured downstream
    can't OOM the gate by streaming gigabytes — the stream is aborted
    at the ceiling and `DownstreamResponseTooLarge` propagates.
    """
    url = _downstream_url()
    outbound_headers = dict(headers)  # copy — never mutate caller's dict
    downstream_bearer = os.getenv("MCP_GATE_DOWNSTREAM_BEARER_TOKEN", "")
    if downstream_bearer:
        outbound_headers["Authorization"] = f"Bearer {downstream_bearer}"
    # No-redirect is the standing gateway convention (autonomy webhook
    # executor, remediation) — a downstream MCP server returning 301 to
    # localhost/metadata is not a redirect we chase.
    async with httpx.AsyncClient(
        timeout=_DOWNSTREAM_TIMEOUT_S,
        follow_redirects=False,
    ) as c:
        # Content-Length pre-check — reject at the header if the
        # downstream is honest about the size.
        async with c.stream("POST", url, content=body, headers=outbound_headers) as stream:
            declared = stream.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > _MAX_RESP_BYTES:
                raise DownstreamResponseTooLarge(
                    f"downstream declared {declared} bytes > cap {_MAX_RESP_BYTES}",
                )
            # Post-read: even if the downstream lies or omits Content-Length,
            # we abort the stream when the running total crosses the ceiling.
            chunks: list[bytes] = []
            total = 0
            async for chunk in stream.aiter_bytes():
                total += len(chunk)
                if total > _MAX_RESP_BYTES:
                    raise DownstreamResponseTooLarge(
                        f"downstream streamed > cap {_MAX_RESP_BYTES}",
                    )
                chunks.append(chunk)
            # Assemble a Response object with the read body — matches
            # the shape callers expect (they read `.status_code`, `.json()`,
            # `.text`, `.headers`).
            resp = httpx.Response(
                status_code=stream.status_code,
                headers=stream.headers,
                content=b"".join(chunks),
                request=stream.request,
            )
            return resp


@router.post("/messages")
async def mcp_messages(
    request: Request,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
    x_agent_id:  str | None = Header(default=None, alias="X-Agent-ID"),
) -> JSONResponse:
    """MCP-over-HTTP JSON-RPC entry point.

    Non-tools/call methods forward unchanged; tools/call routes through
    the policy Gate and is ledgered.
    """
    downstream_url = _downstream_url()
    if not downstream_url:
        return JSONResponse(
            status_code=503,
            content={"error": "MCP gate: no downstream configured"},
        )

    # SSRF guard the configured URL — prevents an env-var-compromise
    # attack from turning the proxy into a metadata-fetcher.
    try:
        validate_outbound_url(downstream_url, allowed_schemes=_ALLOWED_SCHEMES)
    except OutboundUrlBlocked as exc:
        logger.critical("mcp_gate_downstream_url_blocked", url=downstream_url, reason=str(exc))
        return JSONResponse(
            status_code=503,
            content={"error": f"MCP gate: downstream URL blocked by SSRF guard: {exc}"},
        )

    # Body-size DoS guard — reject at the Content-Length pre-read when
    # the client is honest, then check post-read as belt-and-suspenders
    # for clients that omit or lie about Content-Length.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": f"body exceeds {_MAX_BODY_BYTES} bytes"},
        )
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"error": f"body exceeds {_MAX_BODY_BYTES} bytes"},
        )
    try:
        body = _safe_json(raw)
        call = parse_mcp_call(body)
    except McpParseError as exc:
        return JSONResponse(
            status_code=200,
            content=json_rpc_error(None, _ERR_INVALID_REQUEST, str(exc)),
        )

    # Non-tool methods (initialize, tools/list, ping, prompts/list…) are
    # metadata — forward verbatim without policy gating.
    if not is_tools_call(call.method):
        try:
            resp = await _forward_to_downstream(raw, _forward_headers(request))
        except DownstreamResponseTooLarge as exc:
            logger.warning("mcp_gate_downstream_response_too_large", reason=str(exc))
            return JSONResponse(
                status_code=502,
                content=json_rpc_error(call.id, _ERR_INTERNAL,
                                       "downstream MCP response exceeded size cap"),
            )
        except httpx.HTTPError as exc:
            return JSONResponse(
                status_code=502,
                content=json_rpc_error(call.id, _ERR_INTERNAL,
                                       f"downstream MCP unreachable: {type(exc).__name__}"),
            )
        return _passthrough(resp)

    # tools/call — extract, gate, forward-or-refuse.
    try:
        tool = extract_tool_call(call.params)
    except McpParseError as exc:
        return JSONResponse(
            status_code=200,
            content=json_rpc_error(call.id, _ERR_INVALID_REQUEST, str(exc)),
        )

    tenant_id = x_tenant_id or ""
    agent_id = x_agent_id or ""
    if not tenant_id or not agent_id:
        return JSONResponse(
            status_code=200,
            content=json_rpc_error(
                call.id, _ERR_INVALID_REQUEST,
                "MCP gate requires X-Tenant-ID + X-Agent-ID",
            ),
        )

    decision = await _call_policy_gate(tenant_id, agent_id, tool.name, tool.arguments)
    if decision is None:
        # Policy service unreachable → fail CLOSED. §3.2 says the gate
        # is the only egress path; if we can't decide, we don't act.
        return JSONResponse(
            status_code=200,
            content=json_rpc_error(
                call.id, _ERR_INTERNAL,
                "aegis_gate: policy service unreachable; failing closed",
            ),
        )

    # Ledger the decision — this is the audit primitive per §5.2 step 6.
    _redis = get_redis_client(settings.REDIS_URL)
    try:
        await push_audit_event(
            redis=_redis, tenant_id=tenant_id, agent_id=agent_id,
            action="mcp_tools_call", tool=tool.name,
            decision=decision.tier,
            reason=decision.reason,
            metadata={
                "action_class":  "C2",   # external MCP tool call
                "policy_id":     decision.policy_id,
                "findings":      list(decision.findings),
                "mcp_method":    "tools/call",
                # Strip userinfo (basic-auth in URL) before persisting to
                # the audit trail. Operators sometimes configure downstream
                # URLs as `https://user:pass@host/path` for legacy MCP
                # servers; that credential would then live in the ledger
                # forever + export bundles.
                "downstream_url": _redact_url_credentials(downstream_url),
            },
        )
    finally:
        await _redis.aclose()

    if decision.tier != "allow":
        return JSONResponse(
            status_code=200,
            content=build_gate_denial(decision, call.id),
        )

    # ALLOWED — forward to downstream, stream the response back.
    try:
        resp = await _forward_to_downstream(raw, _forward_headers(request))
    except DownstreamResponseTooLarge as exc:
        logger.warning("mcp_gate_downstream_response_too_large",
                       gate_decision_id=str(tool.name)[:64], reason=str(exc))
        return JSONResponse(
            status_code=502,
            content=json_rpc_error(call.id, _ERR_INTERNAL,
                                   "downstream MCP response exceeded size cap"),
        )
    except httpx.HTTPError as exc:
        return JSONResponse(
            status_code=502,
            content=json_rpc_error(call.id, _ERR_INTERNAL,
                                   f"downstream MCP unreachable: {type(exc).__name__}"),
        )
    return _passthrough(resp)


def _safe_json(raw: bytes) -> dict[str, Any]:
    import json
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise McpParseError(f"body is not JSON: {exc}") from exc


def _redact_url_credentials(url: str) -> str:
    """Strip userinfo (basic-auth in URL) before logging to the audit
    trail. `https://user:pass@host/path` → `https://host/path`. Leaves
    every other URL component intact — schema, host, port, path,
    query, fragment. Non-URL strings pass through unchanged."""
    from urllib.parse import urlparse, urlunparse
    try:
        parsed = urlparse(url)
    except (ValueError, AttributeError):
        return url
    if not parsed.netloc or "@" not in parsed.netloc:
        return url
    # Rebuild netloc without userinfo.
    host_and_port = parsed.netloc.rsplit("@", 1)[-1]
    return urlunparse(parsed._replace(netloc=host_and_port))


def _forward_headers(request: Request) -> dict[str, str]:
    """Preserve MCP protocol headers + strip Aegis-specific ones.

    SECURITY: `Authorization` is NEVER forwarded — the agent runtime's
    bearer is scoped to the gate, not the downstream. The gate injects
    a downstream-scoped bearer separately if `MCP_GATE_DOWNSTREAM_BEARER_TOKEN`
    is configured (see `_forward_to_downstream`).
    """
    out = {}
    for name in ("content-type", "accept", "mcp-session-id"):
        val = request.headers.get(name)
        if val:
            out[name] = val
    return out


def _passthrough(resp: httpx.Response) -> JSONResponse:
    content_type = resp.headers.get("content-type", "application/json")
    try:
        body = resp.json()
    except ValueError:
        return JSONResponse(
            status_code=resp.status_code,
            content={"raw": resp.text[:8192]},
            media_type=content_type,
        )
    return JSONResponse(status_code=resp.status_code, content=body)
