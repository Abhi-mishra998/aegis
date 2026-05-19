"""Produce a verifiable archive bundle by polling an ACP deployment.

Pairs with `acp verify-bundle`: this command writes the directory layout the
verifier expects. After archiving, the bundle is self-contained — verification
never touches the network.

Output layout:

    <out>/public_key.pem
    <out>/receipts/<execution_id>.json
    <out>/inclusion/<execution_id>.json   (one per row that has a daily root)
    <out>/roots/<YYYY-MM-DD>.json         (one per distinct date in the window)

Resumable: existing files in the output dir are not re-fetched. Safe to run
hourly or daily as a cron.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import httpx


class ArchiveError(Exception):
    """Raised when the archive cannot complete (network, auth, server)."""


def build_archive(
    *,
    base_url: str,
    token: str,
    out_dir: str | Path,
    tenant: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10_000,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> dict[str, int]:
    """Fetch all artifacts in the window and persist to `out_dir`.

    Returns a dict of counts: {receipts, inclusion, roots}. Idempotent — skips
    files that already exist.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    receipts_dir = (out / "receipts"); receipts_dir.mkdir(exist_ok=True)
    inclusion_dir = (out / "inclusion"); inclusion_dir.mkdir(exist_ok=True)
    roots_dir = (out / "roots"); roots_dir.mkdir(exist_ok=True)

    owns_client = client is None
    http = client or httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        headers={
            "Authorization": f"Bearer {token}",
            **({"X-Tenant-ID": tenant} if tenant else {}),
            "User-Agent": "acp-archive/0.2",
        },
    )

    counts = {"receipts": 0, "inclusion": 0, "roots": 0}
    try:
        # 1. Public key — cheap, always refetched (file is tiny, may have rotated).
        _write_pubkey(http, out / "public_key.pem")

        # 2. Stream the export NDJSON for the window. Each row → one receipt + one inclusion.
        seen_dates: set[str] = set()
        for row in _stream_export(http, since=since, until=until, limit=limit):
            exec_id = row.get("id")
            ts = row.get("timestamp", "")
            if not exec_id:
                continue
            date_part = ts[:10] if ts else ""
            seen_dates.add(date_part)

            r_path = receipts_dir / f"{exec_id}.json"
            if not r_path.exists():
                _save_receipt(http, exec_id, r_path)
                counts["receipts"] += 1

            i_path = inclusion_dir / f"{exec_id}.json"
            if not i_path.exists():
                if _save_inclusion(http, exec_id, i_path):
                    counts["inclusion"] += 1

        # 3. Signed daily roots for every date that appeared in the export.
        for d in sorted(seen_dates):
            if not d:
                continue
            root_path = roots_dir / f"{d}.json"
            if root_path.exists():
                continue
            if _save_root(http, d, root_path):
                counts["roots"] += 1

    finally:
        if owns_client:
            http.close()

    return counts


# ── helpers ───────────────────────────────────────────────────────────────


def _raise_for_status(resp: httpx.Response, what: str) -> None:
    if resp.status_code >= 500:
        raise ArchiveError(f"{what}: server error {resp.status_code}: {resp.text[:200]}")
    if resp.status_code in (401, 403):
        raise ArchiveError(f"{what}: authentication failed ({resp.status_code})")


def _write_pubkey(http: httpx.Client, path: Path) -> None:
    resp = http.get("/v1/receipts/key")
    _raise_for_status(resp, "/v1/receipts/key")
    if resp.status_code != 200:
        raise ArchiveError(f"/v1/receipts/key → {resp.status_code}")
    body = resp.json()
    pem = body.get("public_key_pem") or body.get("data", {}).get("public_key_pem")
    if not pem:
        raise ArchiveError("public key response missing public_key_pem")
    path.write_text(pem)


def _stream_export(
    http: httpx.Client,
    *,
    since: str | None,
    until: str | None,
    limit: int,
) -> Iterator[dict[str, Any]]:
    params: dict[str, Any] = {"limit": limit}
    if since: params["since"] = since
    if until: params["until"] = until
    with http.stream("GET", "/v1/audit/export", params=params) as resp:
        _raise_for_status(resp, "/v1/audit/export")
        if resp.status_code != 200:
            raise ArchiveError(f"/v1/audit/export → {resp.status_code}")
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # tolerate the occasional empty/whitespace line in the stream
                continue


def _save_receipt(http: httpx.Client, exec_id: str, path: Path) -> None:
    resp = http.get(f"/v1/receipts/{exec_id}")
    _raise_for_status(resp, f"/v1/receipts/{exec_id}")
    if resp.status_code != 200:
        raise ArchiveError(f"/v1/receipts/{exec_id} → {resp.status_code}")
    body = resp.json()
    payload = body.get("data") if isinstance(body, dict) and "data" in body else body
    path.write_text(json.dumps(payload))


def _save_inclusion(http: httpx.Client, exec_id: str, path: Path) -> bool:
    """Returns True if a proof was written, False if not yet available (404)."""
    resp = http.get(f"/v1/transparency/inclusion/{exec_id}")
    if resp.status_code == 404:
        return False
    _raise_for_status(resp, f"/v1/transparency/inclusion/{exec_id}")
    if resp.status_code != 200:
        raise ArchiveError(f"/v1/transparency/inclusion/{exec_id} → {resp.status_code}")
    body = resp.json()
    payload = body.get("data") if isinstance(body, dict) and "data" in body else body
    # Skip pending proofs — the daily root hasn't been committed yet.
    if isinstance(payload, dict) and payload.get("pending"):
        return False
    path.write_text(json.dumps(payload))
    return True


def _save_root(http: httpx.Client, root_date: str, path: Path) -> bool:
    resp = http.get(f"/v1/transparency/roots/{root_date}")
    if resp.status_code == 404:
        return False
    _raise_for_status(resp, f"/v1/transparency/roots/{root_date}")
    if resp.status_code != 200:
        raise ArchiveError(f"/v1/transparency/roots/{root_date} → {resp.status_code}")
    body = resp.json()
    payload = body.get("data") if isinstance(body, dict) and "data" in body else body
    path.write_text(json.dumps(payload))
    return True
