"""
Sprint 8 — Aegis MCP server (FastMCP wrapper).

Wraps the pure tool implementations in services/mcp_server/tools.py with
the official ``mcp`` SDK so an MCP-aware client (Claude Desktop, Cursor,
the Sprint-8 VS Code extension) can invoke them over the stdio
transport.

Run locally:

    AEGIS_GATEWAY_URL=https://dev.aegisagent.in \
    AEGIS_MCP_API_KEY=<your-aegis-api-key> \
    python -m services.mcp_server

Connect from Claude Desktop:

    {
      "mcpServers": {
        "aegis": {
          "command": "python",
          "args": ["-m", "services.mcp_server"],
          "env": {
            "AEGIS_GATEWAY_URL": "https://dev.aegisagent.in",
            "AEGIS_MCP_API_KEY": "<your-aegis-api-key>"
          }
        }
      }
    }

The API key is read once at startup; if it's missing the server boots
but every tool call returns the canonical
``{error: True, code: "auth.missing_key"}`` payload so the MCP client
gets a useful message rather than a stack trace.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import structlog

from services.mcp_server.tools import (
    ToolError,
    evaluate_action,
    fetch_receipt,
    query_blast_radius,
    verify_chain,
)

logger = structlog.get_logger(__name__)


_DESCRIPTION = (
    "Aegis runtime governance — evaluate actions against policy, fetch "
    "signed receipts, verify the cryptographic audit chain, and query "
    "blast-radius. Backed by the Aegis gateway over the buyer's existing "
    "tenant API key."
)


def _missing_key_error() -> dict[str, Any]:
    return ToolError(
        code="auth.missing_key",
        message=(
            "AEGIS_MCP_API_KEY env var is unset. Create a key at "
            "POST /api-keys in the Aegis admin console and supply it to "
            "the MCP server's environment."
        ),
    ).to_dict()


def _wrap_async(fn):
    """Return a thin async wrapper that turns ToolError into a structured
    dict so the MCP client receives a useful response instead of an
    exception stack."""
    async def _inner(**kwargs: Any) -> dict[str, Any]:
        key = os.getenv("AEGIS_MCP_API_KEY") or kwargs.pop("api_key", None)
        if not key:
            return _missing_key_error()
        try:
            return await fn(api_key=key, **kwargs)
        except ToolError as exc:
            return exc.to_dict()
        except Exception as exc:  # noqa: BLE001
            logger.exception("mcp_tool_unexpected", tool=fn.__name__)
            return {
                "error": True,
                "code": "internal_error",
                "message": str(exc),
            }
    _inner.__name__ = fn.__name__
    _inner.__doc__ = fn.__doc__
    return _inner


def build_server() -> Any:
    """Build (but don't start) the FastMCP server. Raises ImportError if
    the ``mcp`` SDK isn't installed in the current venv — callers can
    catch it to print an install hint."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        name="aegis-governance",
        instructions=_DESCRIPTION,
    )

    @mcp.tool(
        name="aegis.evaluate_action",
        description=(
            "Evaluate an agent action against the live Aegis pipeline. "
            "Returns allow|deny|throttle|escalate plus risk and findings "
            "BEFORE the action runs."
        ),
    )
    async def _evaluate_action(agent_id: str, tool: str, payload: str) -> dict:
        return await _wrap_async(evaluate_action)(
            agent_id=agent_id, tool=tool, payload=payload,
        )

    @mcp.tool(
        name="aegis.fetch_receipt",
        description="Return the ed25519-signed receipt for a past execution_id.",
    )
    async def _fetch_receipt(execution_id: str) -> dict:
        return await _wrap_async(fetch_receipt)(execution_id=execution_id)

    @mcp.tool(
        name="aegis.verify_chain",
        description=(
            "Stream the audit chain over a window and verify per-shard "
            "prev_hash linkage + per-row event_hash. Detects truncation "
            "and tampering offline."
        ),
    )
    async def _verify_chain(
        since: str | None = None,
        until: str | None = None,
        limit: int = 1000,
    ) -> dict:
        return await _wrap_async(verify_chain)(
            since=since, until=until, limit=limit,
        )

    @mcp.tool(
        name="aegis.query_blast_radius",
        description=(
            "Identity-graph BFS rooted at an agent_id — 'if this agent is "
            "compromised, what does it reach?' depth defaults to 3."
        ),
    )
    async def _query_blast_radius(agent_id: str, depth: int = 3) -> dict:
        return await _wrap_async(query_blast_radius)(
            agent_id=agent_id, depth=depth,
        )

    return mcp


def main() -> None:
    """Stdio transport entry point — the canonical way MCP servers run."""
    try:
        server = build_server()
    except ImportError:
        sys.stderr.write(
            "aegis-mcp-server: the 'mcp' package is not installed. "
            "Install with: pip install 'mcp>=0.5'\n"
        )
        sys.exit(2)
    logger.info(
        "aegis_mcp_starting",
        gateway=os.getenv("AEGIS_GATEWAY_URL", "http://localhost:8000"),
        has_api_key=bool(os.getenv("AEGIS_MCP_API_KEY")),
    )
    server.run()


if __name__ == "__main__":
    main()
