"""archive → verify roundtrip via httpx MockTransport.

Models a real ACP deployment in-process: the export endpoint streams NDJSON,
receipt endpoints sign with the audit signer, transparency endpoints serve a
real signed daily root + Merkle inclusion proofs. The archive client pulls
the lot and writes the on-disk bundle layout; we then verify it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from sdk.acp_client import verify_inclusion, verify_receipt, leaf_hash_for_receipt
from sdk.acp_client.archive import ArchiveError, build_archive
from services.audit.merkle import build_root, inclusion_proof, leaf_hash
from services.audit.signer import canonical_json, get_signer, reset_signer_for_tests
from services.audit.transparency import _sign_root


@pytest.fixture
def isolated_signer(tmp_path, monkeypatch):
    monkeypatch.setenv("RECEIPT_SIGNING_KEY_PATH", str(tmp_path / "signing-key.pem"))
    monkeypatch.delenv("RECEIPT_SIGNING_PRIVATE_KEY", raising=False)
    reset_signer_for_tests()
    yield get_signer()
    reset_signer_for_tests()


def _build_in_memory_acp(signer, n: int = 5):
    """Return (rows, payloads, leaves, root_hex, day, tenant_id, handler)."""
    tenant_id = uuid.UUID(int=2026)
    rows = []
    for i in range(n):
        rows.append({
            "id":          uuid.UUID(int=i + 1),
            "tenant_id":   tenant_id,
            "agent_id":    uuid.UUID(int=42),
            "tool":        f"tool_{i}",
            "action":      "execute",
            "decision":    "allow",
            "reason":      None,
            "request_id":  f"req_{i}",
            "timestamp":   datetime(2026, 5, 14, 10, i, 0, tzinfo=UTC),
            "event_hash":  f"{i:064d}",
            "prev_hash":   None if i == 0 else f"{i-1:064d}",
            "chain_shard": 0,
        })
    payloads = [signer.sign(r) for r in rows]
    leaves = [leaf_hash(canonical_json(p)) for p in payloads]
    root_hex = build_root(leaves)
    day = rows[0]["timestamp"].date()
    signed_root = _sign_root(tenant_id, day, root_hex, len(leaves))
    by_id = {p["receipt"]["execution_id"]: p for p in payloads}
    by_index = {p["receipt"]["execution_id"]: i for i, p in enumerate(payloads)}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/receipts/key":
            return httpx.Response(200, json={
                "algorithm":      "ed25519",
                "public_key_pem": signer.public_key_pem(),
                "fingerprint":    payloads[0]["public_key_fingerprint"],
                "created_at":     datetime.now(UTC).isoformat(),
            })
        if path == "/v1/audit/export":
            body = b"".join(
                (json.dumps({
                    "id":           str(r["id"]),
                    "timestamp":    r["timestamp"].isoformat(),
                    "tenant_id":    str(tenant_id),
                    "agent_id":     str(r["agent_id"]),
                    "tool":         r["tool"],
                    "decision":     r["decision"],
                }) + "\n").encode()
                for r in rows
            )
            return httpx.Response(200, content=body, headers={"content-type": "application/x-ndjson"})
        if path.startswith("/v1/receipts/"):
            exec_id = path.split("/")[-1]
            payload = by_id.get(exec_id)
            if not payload:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": payload})
        if path.startswith("/v1/transparency/inclusion/"):
            exec_id = path.split("/")[-1]
            idx = by_index.get(exec_id)
            if idx is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"data": {
                "root_date": day.isoformat(),
                "proof":     inclusion_proof(leaves, idx),
                "pending":   False,
            }})
        if path.startswith("/v1/transparency/roots/"):
            d = path.split("/")[-1]
            if d != day.isoformat():
                return httpx.Response(404)
            return httpx.Response(200, json={"data": {
                "root_date":  day.isoformat(),
                "root_hash":  root_hex,
                "leaf_count": len(leaves),
                "signed":     signed_root,
            }})
        return httpx.Response(404, json={"error": f"unhandled {path}"})

    return rows, payloads, leaves, root_hex, day, tenant_id, handler


def _stub_client(handler, base_url="https://acp.test", token="test"):
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "acp-archive/0.2",
        },
    )


def test_archive_writes_complete_bundle(tmp_path, isolated_signer):
    _, payloads, _, _, day, _, handler = _build_in_memory_acp(isolated_signer, n=5)
    out = tmp_path / "bundle"
    counts = build_archive(
        base_url="https://acp.test",
        token="test",
        out_dir=out,
        client=_stub_client(handler),
    )
    assert counts == {"receipts": 5, "inclusion": 5, "roots": 1}
    assert (out / "public_key.pem").exists()
    assert len(list((out / "receipts").glob("*.json"))) == 5
    assert len(list((out / "inclusion").glob("*.json"))) == 5
    assert (out / "roots" / f"{day.isoformat()}.json").exists()


def test_archive_then_verify_roundtrip_via_cli(tmp_path, isolated_signer):
    _, _, _, _, _, _, handler = _build_in_memory_acp(isolated_signer, n=4)
    out = tmp_path / "bundle"
    build_archive(
        base_url="https://acp.test",
        token="test",
        out_dir=out,
        client=_stub_client(handler),
    )
    result = subprocess.run(
        [sys.executable, "-m", "sdk.acp_client.cli", "verify-bundle", str(out), "--json"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["counts"]["receipts_ok"] == 4
    assert payload["counts"]["inclusion_ok"] == 4
    assert payload["counts"]["root_matches"] == 4


def test_archive_is_idempotent_skips_existing(tmp_path, isolated_signer):
    _, _, _, _, _, _, handler = _build_in_memory_acp(isolated_signer, n=3)
    out = tmp_path / "bundle"
    build_archive(
        base_url="https://acp.test",
        token="test",
        out_dir=out,
        client=_stub_client(handler),
    )
    # Second run should be a no-op (every file already present).
    counts = build_archive(
        base_url="https://acp.test",
        token="test",
        out_dir=out,
        client=_stub_client(handler),
    )
    assert counts == {"receipts": 0, "inclusion": 0, "roots": 0}


def test_archive_raises_on_auth_failure(tmp_path, isolated_signer):
    def bad_handler(_request):
        return httpx.Response(401, json={"error": "bad token"})

    with pytest.raises(ArchiveError, match="auth"):
        build_archive(
            base_url="https://acp.test",
            token="wrong",
            out_dir=tmp_path / "bundle",
            client=_stub_client(bad_handler),
        )


def test_archive_tolerates_pending_inclusion(tmp_path, isolated_signer):
    """Inclusion proof returns pending=true → file is NOT written, archive succeeds."""
    rows, payloads, leaves, root_hex, day, tenant_id, _ = _build_in_memory_acp(isolated_signer, n=3)

    def pending_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/receipts/key":
            return httpx.Response(200, json={
                "algorithm":      "ed25519",
                "public_key_pem": isolated_signer.public_key_pem(),
                "fingerprint":    payloads[0]["public_key_fingerprint"],
                "created_at":     datetime.now(UTC).isoformat(),
            })
        if path == "/v1/audit/export":
            body = b"".join(
                (json.dumps({"id": str(r["id"]), "timestamp": r["timestamp"].isoformat()}) + "\n").encode()
                for r in rows
            )
            return httpx.Response(200, content=body)
        if path.startswith("/v1/receipts/"):
            exec_id = path.split("/")[-1]
            for p in payloads:
                if p["receipt"]["execution_id"] == exec_id:
                    return httpx.Response(200, json={"data": p})
            return httpx.Response(404)
        if path.startswith("/v1/transparency/inclusion/"):
            return httpx.Response(200, json={"data": {"pending": True, "root_date": day.isoformat()}})
        if path.startswith("/v1/transparency/roots/"):
            return httpx.Response(404)
        return httpx.Response(404)

    out = tmp_path / "bundle"
    counts = build_archive(
        base_url="https://acp.test",
        token="test",
        out_dir=out,
        client=_stub_client(pending_handler),
    )
    assert counts["receipts"] == 3
    assert counts["inclusion"] == 0   # pending → skipped
    assert counts["roots"] == 0       # 404 → skipped
