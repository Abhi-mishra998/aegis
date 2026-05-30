"""Gateway proxy routes for cryptographic receipts + the daily Merkle
root transparency log.

11 routes lifted out of services/gateway/main.py in the sprint-5 audit
cleanup. The two prefixes share a module because they describe the
same offline-verifiable trust chain:

  /receipts/*        — per-execution ed25519 signed receipts
                       (customers verify offline with the public key)
  /transparency/*    — daily Merkle root commitment over the day's
                       receipts, signed by a separate root-signing key

GET /receipts/{execution_id} keeps its envelope-flattening + sibling
``fingerprint`` alias so direct-HTTP probes see the signed shape at the
top level — the customer-reported "Gap 1" symptom was caused by probes
checking the wrapper instead of the inner data dict.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sdk.common.config import settings
from services.gateway._helpers import internal_headers, passthrough

router = APIRouter()


def _base() -> str:
    return settings.AUDIT_SERVICE_URL.rstrip("/")


# ── Receipts ─────────────────────────────────────────────────────────────

@router.get("/receipts/key", tags=["receipts"])
async def receipts_public_key(request: Request) -> Any:
    """Proxy → Audit signer public key. Cache this client-side."""
    resp = await request.app.state.client.get(
        f"{_base()}/receipts/key",
        headers=internal_headers(request),
    )
    return passthrough(resp)


# /receipts/verify must precede /receipts/{execution_id} so FastAPI does
# not greedily match the literal string "verify" as an execution_id.
@router.post("/receipts/verify", tags=["receipts"])
async def receipts_verify(request: Request) -> Any:
    """Proxy → Audit receipt verifier. Body is the signed receipt payload."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_base()}/receipts/verify",
        content=body,
        headers={**internal_headers(request), "Content-Type": "application/json"},
    )
    return passthrough(resp)


@router.get("/receipts/{execution_id}", tags=["receipts"])
async def get_execution_receipt(execution_id: str, request: Request) -> Any:
    """Proxy → Audit service signed receipt for one audit row.

    The execution_id is the audit row id (matches what Flight Recorder
    surfaces and what the SDK's ``protect()`` decorator records).

    2026-05-15: the upstream returns ``APIResponse(data={receipt, signature,
    algorithm, public_key_fingerprint})``. External auditors curl this
    endpoint and want those fields at the top level (the customer-reported
    Gap 1 symptom was ``{algorithm: null, fingerprint: null, sig_len: 0}``
    — that came from probing the wrapper, not the inner payload). Flatten
    the envelope here so direct-HTTP probes see the signed shape immediately
    while SDK consumers (which already unwrap ``data``) are unaffected.
    """
    resp = await request.app.state.client.get(
        f"{_base()}/logs/{execution_id}/receipt",
        headers=internal_headers(request),
    )
    if resp.status_code >= 400:
        return passthrough(resp)
    try:
        body = resp.json()
    except Exception:
        return passthrough(resp)
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        inner = body["data"]
        return JSONResponse(
            status_code=resp.status_code,
            content={
                **inner,
                # Sibling `fingerprint` alias so historical probe scripts
                # that check `payload.fingerprint` resolve, while
                # `public_key_fingerprint` (the canonical offline-verifier
                # field) is preserved.
                "fingerprint": inner.get("public_key_fingerprint"),
            },
        )
    return passthrough(resp)


# ── Transparency log (daily Merkle root commitment) ──────────────────────

@router.get("/transparency/key", tags=["transparency"])
async def transparency_root_public_key(request: Request) -> Any:
    """Proxy → Audit root-signing public key.

    Distinct from /receipts/key. Customers archive both: the receipt
    key to verify per-receipt signatures, the root key to verify daily
    roots.
    """
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/key",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/transparency/keys", tags=["transparency"])
async def transparency_keys(request: Request) -> Any:
    """Proxy → Audit root-signing key directory (active + historical)."""
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/keys",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/transparency/roots", tags=["transparency"])
async def transparency_list_roots(request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/roots",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/transparency/roots/{root_date}", tags=["transparency"])
async def transparency_get_root(root_date: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/roots/{root_date}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/transparency/compute", tags=["transparency"])
async def transparency_compute_root(request: Request) -> Any:
    """Trigger (re)computation of a daily root. Idempotent.

    Typical use: a daily cron at 00:05 UTC calls this with no body to
    commit yesterday's events. Operators may also call ad-hoc for backfill.
    """
    resp = await request.app.state.client.post(
        f"{_base()}/transparency/compute",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/transparency/inclusion/{execution_id}", tags=["transparency"])
async def transparency_inclusion(execution_id: str, request: Request) -> Any:
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/inclusion/{execution_id}",
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.get("/transparency/consistency", tags=["transparency"])
async def transparency_consistency(request: Request) -> Any:
    """Proxy → Audit consistency proof. Returns the chain of
    ``root_hash`` + ``prev_root_hash`` records so the caller can verify
    the log is append-only between two snapshots."""
    resp = await request.app.state.client.get(
        f"{_base()}/transparency/consistency",
        params=dict(request.query_params),
        headers=internal_headers(request),
    )
    return passthrough(resp)


@router.post("/transparency/verify-root", tags=["transparency"])
async def transparency_verify_root(request: Request) -> Any:
    """Proxy → Audit signed-root verifier. Body is the signed root payload."""
    body = await request.body()
    resp = await request.app.state.client.post(
        f"{_base()}/transparency/verify-root",
        content=body,
        headers={**internal_headers(request), "Content-Type": "application/json"},
    )
    return passthrough(resp)
