"""ATF §5.1 MCP proxy logic — pure functions + one HTTP call.

Splits into three concerns that can be tested independently:

  1. `parse_mcp_call(body)` — extracts (method, params, id) from a
     JSON-RPC 2.0 request.
  2. `extract_tool_call(params)` — pulls (tool_name, args) from a
     `tools/call` params block.
  3. `apply_gate_decision(decision, request_id)` — turns the Gate's
     verdict into a JSON-RPC response (allow → forward; deny → error).

Keeps the FastAPI router thin — the router is just HTTP + orchestration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# JSON-RPC 2.0 error codes we use. -32000..-32099 is reserved for server errors.
_ERR_INVALID_REQUEST     = -32600
_ERR_METHOD_NOT_FOUND    = -32601
_ERR_INVALID_PARAMS      = -32602
_ERR_INTERNAL            = -32603
_ERR_GATE_DENIED         = -32001   # in server-error range
_ERR_GATE_ESCALATE       = -32002


@dataclass(frozen=True)
class McpCall:
    jsonrpc: str
    method: str
    params: dict[str, Any]
    id:     str | int | None


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


class McpParseError(Exception):
    """Malformed JSON-RPC. Router returns a JSON-RPC error, HTTP 200."""


def parse_mcp_call(body: dict[str, Any]) -> McpCall:
    """Parse a JSON-RPC 2.0 request. Refuses anything that doesn't
    match the shape — no coercion, no fallback."""
    if not isinstance(body, dict):
        raise McpParseError("body must be a JSON object")
    if body.get("jsonrpc") != "2.0":
        raise McpParseError("jsonrpc must be '2.0'")
    method = body.get("method")
    if not isinstance(method, str) or not method:
        raise McpParseError("method missing or not a string")
    params = body.get("params")
    if params is not None and not isinstance(params, dict):
        raise McpParseError("params must be an object when present")
    rid = body.get("id")
    if rid is not None and not isinstance(rid, (str, int)):
        raise McpParseError("id must be a string, int, or absent")
    return McpCall(
        jsonrpc="2.0",
        method=method,
        params=params or {},
        id=rid,
    )


def extract_tool_call(params: dict[str, Any]) -> ToolCall:
    """Pulls (name, arguments) out of a `tools/call` params block per
    the MCP protocol spec. Refuses missing/malformed name."""
    name = params.get("name")
    if not isinstance(name, str) or not name.strip():
        raise McpParseError("tools/call params must include a non-empty `name`")
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        raise McpParseError("tools/call params.arguments must be an object")
    return ToolCall(name=name.strip(), arguments=args)


@dataclass(frozen=True)
class GateDecision:
    tier: Literal["allow", "monitor", "escalate", "deny", "quarantine"]
    reason: str
    policy_id: str
    findings: list[str]


def json_rpc_error(request_id: str | int | None, code: int, message: str,
                   data: dict[str, Any] | None = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def json_rpc_result(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def build_gate_denial(decision: GateDecision, request_id: str | int | None) -> dict[str, Any]:
    """Turn a deny/escalate/quarantine decision into an MCP-shaped
    JSON-RPC error. Escalate returns a distinct code so a smart MCP
    client can surface the human-approval flow instead of a hard fail."""
    code = _ERR_GATE_ESCALATE if decision.tier == "escalate" else _ERR_GATE_DENIED
    return json_rpc_error(
        request_id, code,
        f"aegis_gate_{decision.tier}: {decision.reason}",
        data={
            "tier":      decision.tier,
            "policy_id": decision.policy_id,
            "findings":  list(decision.findings),
        },
    )


def is_tools_call(method: str) -> bool:
    """MCP tool-invocation methods that need gate mediation. `tools/list`
    and other discovery methods are metadata — forward without gating.

    (An operator who wants to gate discovery too can set
    ``MCP_GATE_DISCOVERY=1`` and use their policy — kept off by default
    because tool discovery is expected read-side.)
    """
    return method == "tools/call"


__all__ = [
    "GateDecision",
    "McpCall",
    "McpParseError",
    "ToolCall",
    "build_gate_denial",
    "extract_tool_call",
    "is_tools_call",
    "json_rpc_error",
    "json_rpc_result",
    "parse_mcp_call",
    "_ERR_GATE_DENIED",
    "_ERR_GATE_ESCALATE",
    "_ERR_INTERNAL",
    "_ERR_INVALID_PARAMS",
    "_ERR_INVALID_REQUEST",
    "_ERR_METHOD_NOT_FOUND",
]
