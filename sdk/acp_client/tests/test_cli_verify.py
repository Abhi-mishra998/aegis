"""End-to-end: build a real archive bundle, verify via `acp verify-bundle`.

Smoke-tests the publicly-shipped verifier CLI. If this passes, the customer
flow ("archive once, verify forever, no network") works.
"""
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def _row(i: int, tenant_id: uuid.UUID):
    return {
        "id":          uuid.UUID(int=i + 1),
        "tenant_id":   tenant_id,
        "agent_id":    uuid.UUID(int=42),
        "tool":        f"tool_{i}",
        "action":      "execute",
        "decision":    "allow",
        "reason":      None,
        "request_id":  f"req_{i}",
        "timestamp":   datetime(2026, 5, 14, 12, i, 0, tzinfo=UTC),
        "event_hash":  f"{i:064d}",
        "prev_hash":   f"{i-1:064d}" if i else None,
        "chain_shard": 0,
    }


def _build_bundle(tmp_path: Path, signer, n: int = 5) -> Path:
    """Materialize a real verifiable bundle on disk."""
    bundle = tmp_path / "bundle"
    receipts_dir = bundle / "receipts"
    inclusion_dir = bundle / "inclusion"
    roots_dir = bundle / "roots"
    for d in (receipts_dir, inclusion_dir, roots_dir):
        d.mkdir(parents=True, exist_ok=True)

    (bundle / "public_key.pem").write_text(signer.public_key_pem())

    tenant_id = uuid.UUID(int=999)
    rows = [_row(i, tenant_id) for i in range(n)]
    receipts = [signer.sign(r) for r in rows]
    leaves = [leaf_hash(canonical_json(p)) for p in receipts]
    root_hex = build_root(leaves)
    day = rows[0]["timestamp"].date()

    for _r, payload in zip(rows, receipts, strict=False):
        exec_id = payload["receipt"]["execution_id"]
        (receipts_dir / f"{exec_id}.json").write_text(json.dumps(payload))

    for i, _r in enumerate(rows):
        exec_id = receipts[i]["receipt"]["execution_id"]
        proof = inclusion_proof(leaves, i)
        (inclusion_dir / f"{exec_id}.json").write_text(json.dumps({
            "root_date": day.isoformat(),
            "proof":     proof,
            "pending":   False,
        }))

    signed_root = _sign_root(tenant_id, day, root_hex, len(leaves))
    (roots_dir / f"{day.isoformat()}.json").write_text(json.dumps({
        "root_date":  day.isoformat(),
        "root_hash":  root_hex,
        "leaf_count": len(leaves),
        "signed":     signed_root,
    }))
    return bundle


def _run_cli(*args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "sdk.acp_client.cli", *args],
        cwd=cwd or os.getcwd(),
        capture_output=True,
        text=True,
    )


def test_verify_bundle_happy_path(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=5)
    result = _run_cli("verify-bundle", str(bundle), "--json")
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["counts"]["receipts_ok"] == 5
    assert payload["counts"]["inclusion_ok"] == 5
    # `root_matches` counts per-proof anchoring — all 5 proofs anchor to
    # their (single) signed daily root.
    assert payload["counts"]["root_anchored"] == 5
    assert payload["counts"]["root_matches"] == 5


def test_verify_bundle_detects_tampered_receipt(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=3)

    # Tamper the first receipt: flip 'decision' from allow → deny.
    receipts_dir = bundle / "receipts"
    target = next(receipts_dir.glob("*.json"))
    payload = json.loads(target.read_text())
    payload["receipt"]["decision"] = "deny"
    target.write_text(json.dumps(payload))

    result = _run_cli("verify-bundle", str(bundle), "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("signature INVALID" in f for f in payload["failures"])


def test_verify_bundle_detects_missing_public_key(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=2)
    (bundle / "public_key.pem").unlink()
    result = _run_cli("verify-bundle", str(bundle), "--json")
    assert result.returncode == 1


def test_verify_bundle_human_readable_output(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=2)
    result = _run_cli("verify-bundle", str(bundle))
    assert result.returncode == 0
    assert "receipts:             2/2" in result.stdout
    assert "OK" in result.stdout.splitlines()[-1]


def test_verify_receipt_subcommand(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=1)
    receipt_file = next((bundle / "receipts").glob("*.json"))
    pubkey_file = bundle / "public_key.pem"

    result = _run_cli("verify-receipt", str(receipt_file), "--pubkey", str(pubkey_file), "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True


def test_verify_inclusion_subcommand(tmp_path, isolated_signer) -> None:
    bundle = _build_bundle(tmp_path, isolated_signer, n=3)
    inclusion_file = next((bundle / "inclusion").glob("*.json"))

    result = _run_cli("verify-inclusion", str(inclusion_file), "--json")
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
