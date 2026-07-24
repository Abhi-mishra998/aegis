"""ATF v3.2 §5.1 — MCP server-side proxy.

Sits BETWEEN the agent runtime and a downstream MCP server, intercepts
every ``tools/call`` JSON-RPC request, evaluates it via the Aegis Policy
service (Gate), and forwards to the downstream ONLY if allowed. Every
call is ledgered — the Gate + Ledger contract holds for MCP-native tool
invocations the same way it does for HTTP proxy calls.

This is a SEPARATE service from ``services/mcp_server/`` (which is
Aegis-as-MCP-server-for-humans — the tertiary drop-in wrapper). The
gate proxy is the PRIMARY interception point per ATF §5.1.

Deployment shape: one gate per downstream MCP server. Multi-downstream
support arrives when a customer's fleet has ≥ 2 configured MCP servers;
the code path is already gated on ``DOWNSTREAM_MCP_URL`` per instance so
horizontal scale-out is one env-var multiplication.
"""
