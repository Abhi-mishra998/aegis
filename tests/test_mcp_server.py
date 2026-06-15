"""Sprint 8 — MCP server contract tests.

These exercise the pure tool implementations against a fully mocked
gateway. The MCP SDK wrapper (server.py) is a thin adapter; the
contract that the buyer relies on lives entirely in tools.py.

We mock the gateway with ``httpx.MockTransport`` so the assertions
target the EXACT wire interactions: each tool first validates the
API key, then uses the returned tenant_id as ``X-Tenant-ID`` for the
downstream call. A buyer reading these tests learns exactly what HTTP
traffic the MCP server generates.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from services.mcp_server import tools as mcp_tools
from services.mcp_server.tools import (
    ToolError,
    evaluate_action,
    fetch_receipt,
    query_blast_radius,
    verify_chain,
)


TENANT_ID = "00000000-0000-0000-0000-000000000001"


def _install_mock(monkeypatch, handler) -> None:
    """Replace _AegisClient's httpx.AsyncClient() with a mock-transport
    one — keeps the contract identical to production."""
    original_init = mcp_tools._AegisClient.__init__

    def patched(self, api_key: str, *, gateway_url: str | None = None) -> None:
        original_init(self, api_key, gateway_url=gateway_url)
        self._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )

    monkeypatch.setattr(mcp_tools._AegisClient, "__init__", patched)


# ---------------------------------------------------------------------------
# evaluate_action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_action_validates_key_first_then_hits_execute(monkeypatch) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/api-keys/validate":
            assert json.loads(request.content)["api_key"] == "k-test"
            return httpx.Response(
                200,
                json={"data": {"tenant_id": TENANT_ID, "id": "k-1"}},
            )
        if request.url.path == "/execute":
            # The downstream call MUST carry the tenant from the key
            # validation, never one supplied by the caller.
            assert request.headers["X-Tenant-ID"] == TENANT_ID
            assert request.headers["X-API-Key"] == "k-test"
            assert request.headers["X-Agent-ID"] == "agent-1"
            body = json.loads(request.content)
            assert body["tool"] == "tool.read_file"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "decision": {
                            "action": "allow",
                            "risk": 0.1,
                            "confidence": 0.9,
                            "findings": [],
                        },
                    },
                    "execution_id": "exec-1",
                },
            )
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    out = await evaluate_action(
        api_key="k-test",
        agent_id="agent-1",
        tool="tool.read_file",
        payload="docs/README.md",
    )
    assert out["action"] == "allow"
    assert out["risk"] == 0.1
    assert out["tenant_id"] == TENANT_ID
    assert out["receipt_id"] == "exec-1"
    assert ("POST", "/api-keys/validate") in seen
    assert ("POST", "/execute") in seen


@pytest.mark.asyncio
async def test_evaluate_action_deny_returns_canonical_shape(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(
                200, json={"data": {"tenant_id": TENANT_ID}},
            )
        if request.url.path == "/execute":
            return httpx.Response(
                403,
                json={
                    "data": {
                        "decision": {
                            "action": "deny",
                            "risk": 0.95,
                            "findings": ["sql_injection_detected"],
                        },
                    },
                },
            )
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    out = await evaluate_action(
        api_key="k-test",
        agent_id="agent-1",
        tool="tool.sql_query",
        payload="DROP TABLE users;",
    )
    assert out["status"] == 403
    assert out["action"] == "deny"
    assert "sql_injection_detected" in out["findings"]


@pytest.mark.asyncio
async def test_evaluate_action_rejects_bad_key(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(
                401, json={"error": "Invalid or expired API key"},
            )
        return httpx.Response(500, text="should never reach")

    _install_mock(monkeypatch, handler)
    with pytest.raises(ToolError) as exc:
        await evaluate_action(
            api_key="k-bad",
            agent_id="agent-1",
            tool="tool.shell",
            payload="ls",
        )
    assert exc.value.code == "auth.invalid_key"


# ---------------------------------------------------------------------------
# fetch_receipt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_receipt_happy_path(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        if request.url.path == "/receipts/exec-42":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "execution_id":     "exec-42",
                        "tenant_id":        TENANT_ID,
                        "agent_id":         "agent-1",
                        "tool":             "tool.read_file",
                        "decision":         "allow",
                        "signed_at":        "2026-06-13T12:00:00Z",
                        "kid":              "aegis-2026-q2",
                        "signature":        "ed25519:abc",
                        "canonical_payload": {"a": 1, "b": 2},
                    },
                },
            )
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    out = await fetch_receipt(api_key="k-test", execution_id="exec-42")
    assert out["execution_id"] == "exec-42"
    assert out["signing_key_kid"] == "aegis-2026-q2"
    assert out["signature"] == "ed25519:abc"
    # sha256 over canonical JSON is included so a client without the
    # signing key can still confirm payload integrity bit-for-bit.
    assert len(out["canonical_payload_sha256"]) == 64


@pytest.mark.asyncio
async def test_fetch_receipt_404_maps_to_not_found(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        return httpx.Response(404, json={"detail": "Not found"})

    _install_mock(monkeypatch, handler)
    with pytest.raises(ToolError) as exc:
        await fetch_receipt(api_key="k-test", execution_id="nope")
    assert exc.value.code == "receipt.not_found"


# ---------------------------------------------------------------------------
# verify_chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_chain_intact(monkeypatch) -> None:
    chain = [
        {"id": "a", "chain_shard": 0, "prev_hash": None,  "event_hash": "h1"},
        {"id": "b", "chain_shard": 0, "prev_hash": "h1",  "event_hash": "h2"},
        {"id": "c", "chain_shard": 0, "prev_hash": "h2",  "event_hash": "h3"},
    ]
    body = "\n".join(json.dumps(r) for r in chain).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        if request.url.path == "/audit/export":
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    out = await verify_chain(api_key="k-test")
    assert out["rows_seen"] == 3
    assert out["chain_intact"] is True
    assert out["breaks"] == []
    assert out["shards_observed"] == [0]


@pytest.mark.asyncio
async def test_verify_chain_detects_truncation(monkeypatch) -> None:
    # `b` says prev_hash=h1 but the chain skips ahead — that's a hole.
    chain = [
        {"id": "a", "chain_shard": 0, "prev_hash": None,  "event_hash": "h1"},
        {"id": "c", "chain_shard": 0, "prev_hash": "h2",  "event_hash": "h3"},
    ]
    body = "\n".join(json.dumps(r) for r in chain).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        return httpx.Response(200, content=body)

    _install_mock(monkeypatch, handler)
    out = await verify_chain(api_key="k-test")
    assert out["chain_intact"] is False
    assert any(b["audit_id"] == "c" for b in out["breaks"])


@pytest.mark.asyncio
async def test_verify_chain_multi_shard(monkeypatch) -> None:
    chain = [
        {"id": "a", "chain_shard": 0, "prev_hash": None, "event_hash": "h1"},
        {"id": "b", "chain_shard": 1, "prev_hash": None, "event_hash": "h2"},
        {"id": "c", "chain_shard": 0, "prev_hash": "h1", "event_hash": "h3"},
        {"id": "d", "chain_shard": 1, "prev_hash": "h2", "event_hash": "h4"},
    ]
    body = "\n".join(json.dumps(r) for r in chain).encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        return httpx.Response(200, content=body)

    _install_mock(monkeypatch, handler)
    out = await verify_chain(api_key="k-test")
    assert out["chain_intact"] is True
    assert sorted(out["shards_observed"]) == [0, 1]


# ---------------------------------------------------------------------------
# query_blast_radius
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blast_radius_returns_nodes_and_edges(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        if request.url.path == "/graph/blast-radius/agent-X":
            assert request.url.params.get("depth") == "2"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "nodes": [{"id": "agent-X"}, {"id": "tool.shell"}],
                        "edges": [{"from": "agent-X", "to": "tool.shell"}],
                    },
                },
            )
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    out = await query_blast_radius(api_key="k-test", agent_id="agent-X", depth=2)
    assert out["agent_id"] == "agent-X"
    assert out["depth"] == 2
    assert len(out["nodes"]) == 2
    assert len(out["edges"]) == 1


@pytest.mark.asyncio
async def test_blast_radius_404_maps_to_graph_not_found(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api-keys/validate":
            return httpx.Response(200, json={"data": {"tenant_id": TENANT_ID}})
        return httpx.Response(404)

    _install_mock(monkeypatch, handler)
    with pytest.raises(ToolError) as exc:
        await query_blast_radius(api_key="k-test", agent_id="ghost")
    assert exc.value.code == "graph.not_found"


# ---------------------------------------------------------------------------
# Tool registry — the canonical contract surface
# ---------------------------------------------------------------------------


def test_tools_registry_lists_all_four() -> None:
    names = [name for name, _, _ in mcp_tools.TOOLS]
    assert names == [
        "aegis.evaluate_action",
        "aegis.fetch_receipt",
        "aegis.verify_chain",
        "aegis.query_blast_radius",
    ]
    for name, fn, desc in mcp_tools.TOOLS:
        assert callable(fn)
        assert desc and len(desc) > 20, f"{name} description too short"
