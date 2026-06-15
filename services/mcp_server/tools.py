"""
Sprint 8 — Aegis MCP tool implementations.

PURE Python helpers (no MCP SDK dependency) so the contract tests can
exercise the tool surface without importing the MCP runtime. The
FastMCP wrapper in ``server.py`` just registers these functions as
tools and forwards the call.

Auth model
==========
Each tool takes an explicit ``api_key`` (the buyer's long-lived Aegis
API key, scoped to a tenant). The tool first validates the key via
``POST /api-keys/validate`` and re-uses the returned tenant_id as the
``X-Tenant-ID`` header for every downstream call. We NEVER trust the
caller-provided tenant; the API-key lookup is the source of truth.

Gateway endpoints used (all behind tenant-scoped API-key auth):

  POST /execute                    — evaluate_action
  GET  /receipts/{execution_id}    — fetch_receipt
  GET  /audit/export?since=...&until=...  — verify_chain (offline-style stream)
  GET  /graph/blast-radius/{node_id}?depth=N  — query_blast_radius

All four return structured dicts that an MCP client (Claude Desktop /
Cursor) can render without further parsing.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


_GATEWAY_URL = os.getenv(
    "AEGIS_MCP_GATEWAY_URL",
    os.getenv("AEGIS_GATEWAY_URL", "http://localhost:8000"),
).rstrip("/")
_TIMEOUT = float(os.getenv("AEGIS_MCP_TIMEOUT", "10.0"))


@dataclass
class ToolError(Exception):
    """All MCP tool failures funnel through this so the SDK wrapper can
    surface a consistent ``{"error": ...}`` payload to the client."""

    code:    str
    message: str
    detail:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error":   True,
            "code":    self.code,
            "message": self.message,
            "detail":  self.detail,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _AegisClient:
    """Thin httpx wrapper that resolves the tenant from the API key once
    and reuses the same headers for every subsequent call."""

    def __init__(self, api_key: str, *, gateway_url: str | None = None) -> None:
        self._api_key = api_key
        self._base = (gateway_url or _GATEWAY_URL).rstrip("/")
        self._tenant_id: str | None = None
        self._client = httpx.AsyncClient(timeout=_TIMEOUT)

    async def __aenter__(self) -> "_AegisClient":
        await self._validate_key()
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _validate_key(self) -> None:
        url = f"{self._base}/api-keys/validate"
        resp = await self._client.post(url, json={"api_key": self._api_key})
        if resp.status_code != 200:
            raise ToolError(
                code="auth.invalid_key",
                message="Aegis API key was rejected by the gateway.",
                detail={"status": resp.status_code, "body": resp.text[:300]},
            )
        body = resp.json()
        data = body.get("data") or body
        tenant = data.get("tenant_id")
        if not tenant:
            raise ToolError(
                code="auth.missing_tenant",
                message="API key validated but no tenant_id in response.",
                detail={"data": data},
            )
        self._tenant_id = str(tenant)

    @property
    def tenant_id(self) -> str:
        if not self._tenant_id:
            raise RuntimeError("Aegis client used before key validation")
        return self._tenant_id

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        h = {
            "X-API-Key":    self._api_key,
            "X-Tenant-ID":  self.tenant_id,
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    async def get(self, path: str, *, params: dict | None = None) -> httpx.Response:
        return await self._client.get(
            f"{self._base}{path}", params=params, headers=self._headers(),
        )

    async def post(self, path: str, *, json_body: dict | None = None,
                   extra_headers: dict[str, str] | None = None) -> httpx.Response:
        return await self._client.post(
            f"{self._base}{path}",
            json=json_body or {},
            headers=self._headers(extra_headers),
        )

    async def stream(self, path: str, *, params: dict | None = None):
        return self._client.stream(
            "GET", f"{self._base}{path}",
            params=params, headers=self._headers(),
        )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def evaluate_action(
    api_key: str,
    *,
    agent_id: str,
    tool: str,
    payload: str | dict[str, Any],
) -> dict[str, Any]:
    """Evaluate an action against the live Aegis pipeline.

    Returns the canonical decision (allow / deny / throttle / escalate /
    kill), risk score, findings, and the receipt id if one was minted.
    An MCP-aware agent can wire this in BEFORE executing a destructive
    action — Aegis returns the verdict before the action runs.
    """
    body: dict[str, Any] = {
        "tool":     tool,
        "agent_id": agent_id,
        "payload":  payload if not isinstance(payload, str) else {"raw": payload},
    }
    async with _AegisClient(api_key) as client:
        resp = await client.post(
            "/execute",
            json_body=body,
            extra_headers={"X-Agent-ID": agent_id},
        )
        if resp.status_code in (200, 403, 429, 504):
            try:
                parsed = resp.json()
            except Exception:
                parsed = {}
            decision = (
                (parsed.get("data") or {}).get("decision")
                or parsed.get("decision")
                or {}
            )
            return {
                "status":      resp.status_code,
                "tenant_id":   client.tenant_id,
                "agent_id":    agent_id,
                "tool":        tool,
                "action":      decision.get("action") or _action_from_status(resp.status_code),
                "risk":        decision.get("risk"),
                "confidence":  decision.get("confidence"),
                "findings":    decision.get("findings") or decision.get("reasons") or [],
                "receipt_id":  parsed.get("execution_id") or parsed.get("receipt_id"),
                "raw":         parsed,
            }
        raise ToolError(
            code=f"http.{resp.status_code}",
            message=f"evaluate_action: unexpected HTTP {resp.status_code}",
            detail={"body": resp.text[:500]},
        )


def _action_from_status(status: int) -> str:
    if status == 200:
        return "allow"
    if status == 403:
        return "deny"
    if status == 429:
        return "throttle"
    return "error"


async def fetch_receipt(
    api_key: str,
    *,
    execution_id: str,
) -> dict[str, Any]:
    """Return the signed receipt for a past execution.

    Receipts carry ed25519 signatures over canonical JSON; the client
    can re-verify them offline using the published Aegis signing key
    via verify_receipt() in sdk/acp_client.
    """
    async with _AegisClient(api_key) as client:
        resp = await client.get(f"/receipts/{execution_id}")
        if resp.status_code == 200:
            parsed = resp.json()
            data = parsed.get("data") or parsed
            return {
                "execution_id":         data.get("execution_id") or execution_id,
                "tenant_id":            data.get("tenant_id") or client.tenant_id,
                "agent_id":             data.get("agent_id"),
                "tool":                 data.get("tool"),
                "decision":             data.get("decision"),
                "signed_at":            data.get("signed_at"),
                "signing_key_kid":      data.get("kid") or data.get("signing_key_kid"),
                "signature":            data.get("signature"),
                "canonical_payload_sha256": _sha256(data.get("canonical_payload")),
                "raw":                  data,
            }
        if resp.status_code == 404:
            raise ToolError(
                code="receipt.not_found",
                message=f"No receipt for execution_id={execution_id}",
            )
        raise ToolError(
            code=f"http.{resp.status_code}",
            message="fetch_receipt: unexpected response",
            detail={"body": resp.text[:500]},
        )


def _sha256(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def verify_chain(
    api_key: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    """Stream the audit chain export, re-derive event_hash + prev_hash
    locally, and report any breaks.

    This is the HTTP-side mirror of ``acp verify-chain`` (sdk/acp_client/
    cli.py). We re-implement the chain walk here so MCP clients don't
    need to ship the acp_client wheel — they get truncation / tamper
    detection in pure dict form.
    """
    # Lazy import — sdk.common.audit_hash lives in the same repo but we
    # don't want to add a hard dep at module top so test collection works
    # without the full server image.
    try:
        from sdk.common.audit_hash import compute_event_hash  # type: ignore[import-not-found]
    except Exception:
        compute_event_hash = None  # type: ignore[assignment]

    rows_seen = 0
    chain_broken_at: list[dict[str, Any]] = []
    prev_hash_by_shard: dict[int, str | None] = {}

    async with _AegisClient(api_key) as client:
        params: dict[str, Any] = {"limit": limit}
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        async with await client.stream("/audit/export", params=params) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise ToolError(
                    code=f"http.{response.status_code}",
                    message="audit export stream failed",
                    detail={"body": body[:500].decode("utf-8", errors="replace")},
                )
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows_seen += 1
                shard = int(row.get("chain_shard") or 0)
                expected_prev = prev_hash_by_shard.get(shard)
                actual_prev = row.get("prev_hash")
                if expected_prev is not None and actual_prev != expected_prev:
                    chain_broken_at.append(
                        {
                            "audit_id":     row.get("id"),
                            "chain_shard":  shard,
                            "expected_prev_hash": expected_prev,
                            "actual_prev_hash":   actual_prev,
                        }
                    )
                if compute_event_hash is not None:
                    try:
                        recomputed = compute_event_hash(row)
                    except Exception:
                        recomputed = None
                    if recomputed and recomputed != row.get("event_hash"):
                        chain_broken_at.append(
                            {
                                "audit_id":           row.get("id"),
                                "recomputed_hash":    recomputed,
                                "stored_event_hash":  row.get("event_hash"),
                                "reason":             "event_hash mismatch",
                            }
                        )
                prev_hash_by_shard[shard] = row.get("event_hash")

        return {
            "rows_seen":       rows_seen,
            "shards_observed": sorted(prev_hash_by_shard.keys()),
            "chain_intact":    not chain_broken_at,
            "breaks":          chain_broken_at,
            "since":           since,
            "until":           until,
        }


async def query_blast_radius(
    api_key: str,
    *,
    agent_id: str,
    depth: int = 3,
) -> dict[str, Any]:
    """Identity-graph BFS rooted at an agent_id.

    Returns the typed node + edge lists Aegis identity_graph already
    persists. An MCP client can render this as a graph — "if THIS agent
    is compromised, what does it reach?"
    """
    async with _AegisClient(api_key) as client:
        resp = await client.get(
            f"/graph/blast-radius/{agent_id}",
            params={"depth": int(depth)},
        )
        if resp.status_code == 200:
            parsed = resp.json()
            data = parsed.get("data") or parsed
            return {
                "agent_id":  agent_id,
                "depth":     int(depth),
                "tenant_id": client.tenant_id,
                "nodes":     data.get("nodes") or [],
                "edges":     data.get("edges") or [],
                "raw":       data,
            }
        if resp.status_code == 404:
            raise ToolError(
                code="graph.not_found",
                message=f"No graph node for agent_id={agent_id}",
            )
        raise ToolError(
            code=f"http.{resp.status_code}",
            message="query_blast_radius: unexpected response",
            detail={"body": resp.text[:500]},
        )


# Registry — the MCP server uses this list to register tools without a
# hand-rolled dispatcher. Each entry is (tool_name, callable, description).
TOOLS = [
    (
        "aegis.evaluate_action",
        evaluate_action,
        "Evaluate an agent action against the live Aegis pipeline; returns "
        "allow/deny/throttle/escalate plus risk and findings BEFORE the "
        "action runs.",
    ),
    (
        "aegis.fetch_receipt",
        fetch_receipt,
        "Return the ed25519-signed receipt for a past execution_id.",
    ),
    (
        "aegis.verify_chain",
        verify_chain,
        "Stream the audit chain over a window and verify per-shard "
        "prev_hash linkage + per-row event_hash. Detects truncation and "
        "tampering offline.",
    ),
    (
        "aegis.query_blast_radius",
        query_blast_radius,
        "Identity-graph BFS from an agent_id — 'if this agent is "
        "compromised, what does it reach?'",
    ),
]
