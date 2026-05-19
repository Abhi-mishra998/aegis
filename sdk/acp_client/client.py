from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

from .errors import (
    ACPError,
    DecisionTimeoutError,
    DeniedError,
    EscalationRequiredError,
    RateLimitedError,
)

T = TypeVar("T", bound=Callable[..., Any])


class Client:
    """Thin client over the ACP gateway.

    The protect() decorator is the primary surface. Other methods exist for
    direct calls (audit replay, policy check) when the decorator pattern
    doesn't fit.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        # Accept either an acp_* API key or a JWT bearer token — both are sent
        # as `Authorization: Bearer <value>` so the gateway treats them the same.
        # Env vars (in order): explicit arg → ACP_TOKEN → ACP_API_KEY. We also
        # peek at a local .env file so the quickstart works without manual
        # `export`s — opt-out by setting ACP_NO_DOTENV=1.
        if api_key is None and token is None and not os.environ.get("ACP_NO_DOTENV"):
            _load_dotenv()
        self.api_key = (
            api_key
            or token
            or os.environ.get("ACP_TOKEN")
            or os.environ.get("ACP_API_KEY")
        )
        self.base_url = (base_url or os.environ.get("ACP_BASE_URL") or "http://localhost:8000").rstrip("/")
        if not self.api_key:
            raise ACPError(
                "No ACP credential found. Pass api_key=/token= or set "
                "ACP_API_KEY / ACP_TOKEN. JWT bearer tokens are accepted."
            )
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "acp-python/0.2",
            },
        )

    # ── Decorator ──────────────────────────────────────────────────────────
    def protect(self, *, agent_id: str, tool: str | None = None) -> Callable[[T], T]:
        """Wrap an agent function so every call goes through ACP.

        Example:
            @acp.protect(agent_id="agent_42", tool="db.read")
            def query(sql: str) -> list[dict]:
                ...
        """

        def decorator(fn: T) -> T:
            inferred_tool = tool or fn.__name__

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                # Policy check before execution
                self.execute(
                    agent_id=agent_id,
                    tool=inferred_tool,
                    payload={"args": list(args), "kwargs": kwargs},
                )
                # If we got here, ACP authorised. Run the real function.
                # The gateway also records the audit event; the receipt is async.
                return fn(*args, **kwargs)

            return wrapper  # type: ignore[return-value]

        return decorator

    # ── Direct calls ───────────────────────────────────────────────────────
    def execute(self, *, agent_id: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit an agent action for runtime authorization.

        Returns the decision payload on allow. Raises DeniedError on deny.
        """
        return self._post(
            "/execute",
            json={"tool": tool, "payload": payload},
            headers={"X-Agent-ID": agent_id, "X-ACP-Tool": tool},
        )

    def replay(self, *, execution_id: str) -> dict[str, Any]:
        """Fetch the full replayable timeline for a past execution."""
        return self._get(f"/flight/timeline/{execution_id}")

    def verify_audit(self) -> dict[str, Any]:
        """Run server-side integrity verification across the audit chain."""
        return self._get("/audit/logs/verify")

    def get_receipt(self, execution_id: str) -> dict[str, Any]:
        """Fetch the signed execution receipt for one audit row.

        Returns the full payload `{receipt, signature, algorithm,
        public_key_fingerprint}` suitable for offline verification via
        `acp.verify_receipt(payload, public_key_pem)`.
        """
        return self._get(f"/receipts/{execution_id}")

    def public_key(self) -> dict[str, Any]:
        """Fetch the ed25519 public key used to sign receipts.

        Cache this. Pair it with `verify_receipt(payload, pem)` to verify
        any number of receipts without re-fetching.
        """
        return self._get("/receipts/key")

    # ── Transparency log ──────────────────────────────────────────────────
    def list_transparency_roots(self, *, since: str | None = None, until: str | None = None, limit: int = 90) -> dict[str, Any]:
        """List daily Merkle roots persisted for this tenant."""
        params: list[str] = []
        if since:
            params.append(f"since={since}")
        if until:
            params.append(f"until={until}")
        params.append(f"limit={limit}")
        return self._get("/transparency/roots?" + "&".join(params))

    def get_transparency_root(self, root_date: str) -> dict[str, Any]:
        """Fetch the daily root + signed commitment for one date (YYYY-MM-DD)."""
        return self._get(f"/transparency/roots/{root_date}")

    def get_inclusion_proof(self, execution_id: str) -> dict[str, Any]:
        """Fetch a Merkle inclusion proof for an execution against its day's root."""
        return self._get(f"/transparency/inclusion/{execution_id}")

    def policy_simulate(self, *, agent_id: str, tool: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dry-run a policy decision without producing an audit record."""
        return self._post(
            "/policy/simulate",
            json={"agent_id": agent_id, "tool": tool, "payload": payload},
        )

    # ── Plumbing ───────────────────────────────────────────────────────────
    def _post(self, path: str, **kwargs) -> dict[str, Any]:
        return self._request("POST", path, **kwargs)

    def _get(self, path: str, **kwargs) -> dict[str, Any]:
        return self._request("GET", path, **kwargs)

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        resp = self._http.request(method, path, **kwargs)
        if resp.status_code == 403:
            body = _safe_json(resp)
            # 2026-05-15: `/execute` ESCALATE / autonomy approval-required
            # now return 403 with `error: "approval_required"`. Surface
            # those as a specific subclass so callers can branch without
            # parsing body shapes themselves. `DeniedError` callers keep
            # working — EscalationRequiredError is a subclass.
            error_field = body.get("error") or body.get("reason") or "denied"
            if error_field == "approval_required":
                meta = body.get("meta") or {}
                raise EscalationRequiredError(
                    detail=body.get("detail", "approval required"),
                    decision_id=meta.get("request_id"),
                    contract_id=meta.get("contract_id"),
                )
            raise DeniedError(
                reason=error_field,
                detail=body.get("detail", "policy denied this action"),
                decision_id=body.get("decision_id"),
            )
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            raise RateLimitedError(retry_after=float(retry) if retry else None)
        if resp.status_code == 504:
            body = _safe_json(resp)
            meta = body.get("meta") or {}
            raise DecisionTimeoutError(
                detail=body.get("detail", "decision pipeline exceeded the gateway deadline"),
                request_id=meta.get("request_id"),
            )
        if resp.status_code >= 400:
            raise ACPError(f"{method} {path} → {resp.status_code}: {resp.text[:300]}")
        body = _safe_json(resp)
        # Gateway wraps many responses in `APIResponse(data=...)` while others
        # return the dict directly. Callers (verify_receipt, public_key access)
        # want the inner payload — unwrap `data` when present so the SDK
        # surface is consistent regardless of the upstream shape.
        if isinstance(body, dict) and "data" in body and isinstance(body["data"], dict):
            return body["data"]
        return body

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        return {}


def _load_dotenv() -> None:
    """Best-effort .env loader so quickstart works without manual `export`s.

    Searches CWD and parents for a `.env` file, populates os.environ for keys
    not already set. Only ACP_* vars are loaded — we don't shadow application
    secrets. Silent on parse failures; this is convenience, not configuration.
    """
    cwd = os.getcwd()
    for _ in range(4):
        candidate = os.path.join(cwd, ".env")
        if os.path.isfile(candidate):
            try:
                with open(candidate) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, val = line.partition("=")
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key.startswith("ACP_") and key not in os.environ:
                            os.environ[key] = val
            except OSError:
                pass
            return
        parent = os.path.dirname(cwd)
        if parent == cwd:
            return
        cwd = parent
