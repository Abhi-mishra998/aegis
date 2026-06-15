"""Sprint 8 — Aegis MCP server.

Exposes four governance tools to MCP-aware clients (Claude Desktop,
Cursor, the Sprint-8 VS Code extension):

  * evaluate_action      — run /execute, return canonical decision + receipt id
  * fetch_receipt        — return the signed receipt for a past execution
  * verify_chain         — verify the audit chain over a window (since/until)
  * query_blast_radius   — identity-graph BFS from an agent_id

Auth is by long-lived Aegis API key (see services/api/router/api_key.py).
The server forwards every tool call through the live gateway, so the
same multi-tenant guardrails (rate limits, JWT scope, policy enforce,
audit logging) apply automatically.
"""
