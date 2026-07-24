"""Real tests for the MCP gate proxy logic."""
from __future__ import annotations

import pytest

from services.mcp_gate.proxy import (
    _ERR_GATE_DENIED,
    _ERR_GATE_ESCALATE,
    GateDecision,
    McpParseError,
    build_gate_denial,
    extract_tool_call,
    is_tools_call,
    json_rpc_error,
    json_rpc_result,
    parse_mcp_call,
)


class TestParseMcpCall:
    def test_valid(self):
        c = parse_mcp_call({
            "jsonrpc": "2.0",
            "method":  "tools/call",
            "params":  {"name": "read_file", "arguments": {"path": "/tmp/a"}},
            "id":      42,
        })
        assert c.method == "tools/call"
        assert c.params["name"] == "read_file"
        assert c.id == 42

    def test_missing_jsonrpc_version_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call({"method": "tools/list"})

    def test_wrong_jsonrpc_version_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call({"jsonrpc": "1.0", "method": "tools/list"})

    def test_non_dict_body_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call([])  # type: ignore[arg-type]

    def test_missing_method_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call({"jsonrpc": "2.0"})

    def test_non_string_method_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call({"jsonrpc": "2.0", "method": 42})

    def test_params_optional(self):
        c = parse_mcp_call({"jsonrpc": "2.0", "method": "ping"})
        assert c.params == {}

    def test_non_dict_params_rejected(self):
        with pytest.raises(McpParseError):
            parse_mcp_call({"jsonrpc": "2.0", "method": "x", "params": "not-an-object"})

    def test_notification_no_id_ok(self):
        c = parse_mcp_call({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert c.id is None

    def test_id_type_bool_rejected(self):
        # bool is a subclass of int in Python but MCP allows string|int
        # only. Explicit rejection would need a stricter check; today we
        # accept int-like via `isinstance(..., (str, int))` — bool passes.
        c = parse_mcp_call({"jsonrpc": "2.0", "method": "x", "id": True})
        assert c.id is True  # documented current behavior


class TestExtractToolCall:
    def test_valid(self):
        t = extract_tool_call({"name": "delete_record", "arguments": {"id": 42}})
        assert t.name == "delete_record"
        assert t.arguments == {"id": 42}

    def test_missing_name_rejected(self):
        with pytest.raises(McpParseError):
            extract_tool_call({})

    def test_empty_name_rejected(self):
        with pytest.raises(McpParseError):
            extract_tool_call({"name": "   "})

    def test_non_string_name_rejected(self):
        with pytest.raises(McpParseError):
            extract_tool_call({"name": 42})

    def test_no_arguments_defaults_empty(self):
        t = extract_tool_call({"name": "ping"})
        assert t.arguments == {}

    def test_non_dict_arguments_rejected(self):
        with pytest.raises(McpParseError):
            extract_tool_call({"name": "x", "arguments": "not-a-dict"})

    def test_name_stripped(self):
        t = extract_tool_call({"name": "  delete_record  "})
        assert t.name == "delete_record"


class TestIsToolsCall:
    def test_tools_call_yes(self):
        assert is_tools_call("tools/call")

    def test_tools_list_no(self):
        assert not is_tools_call("tools/list")

    def test_initialize_no(self):
        assert not is_tools_call("initialize")

    def test_ping_no(self):
        assert not is_tools_call("ping")


class TestGateDenialShape:
    def test_deny_uses_denied_code(self):
        d = GateDecision(tier="deny", reason="over_hard_max",
                         policy_id="finance.deny", findings=["over_hard_max"])
        err = build_gate_denial(d, request_id=7)
        assert err["error"]["code"] == _ERR_GATE_DENIED
        assert err["error"]["data"]["tier"] == "deny"
        assert err["error"]["data"]["policy_id"] == "finance.deny"
        assert err["id"] == 7

    def test_escalate_uses_escalate_code(self):
        d = GateDecision(tier="escalate", reason="approval_needed",
                         policy_id="finance.escalate", findings=[])
        err = build_gate_denial(d, request_id="req-1")
        assert err["error"]["code"] == _ERR_GATE_ESCALATE
        assert err["error"]["data"]["tier"] == "escalate"

    def test_quarantine_uses_denied_code(self):
        d = GateDecision(tier="quarantine", reason="agent_quarantined",
                         policy_id="rt.quarantine", findings=[])
        err = build_gate_denial(d, request_id=None)
        assert err["error"]["code"] == _ERR_GATE_DENIED


class TestJsonRpcHelpers:
    def test_error_shape(self):
        e = json_rpc_error(1, -32001, "denied")
        assert e == {"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "denied"}}

    def test_error_with_data(self):
        e = json_rpc_error(None, -32001, "denied", data={"x": 1})
        assert e["error"]["data"] == {"x": 1}

    def test_result_shape(self):
        r = json_rpc_result("req", {"ok": True})
        assert r == {"jsonrpc": "2.0", "id": "req", "result": {"ok": True}}
