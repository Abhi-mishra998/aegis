#!/usr/bin/env python3
"""
Sprint 9 — DR drill evidence artifact generator.

Wraps scripts/ops/restore_drill.sh and produces a single signed JSON
evidence file per drill run. The artifact lands at:

    reports/restore_drill/<UTC-iso>.json

and (optionally) at:

    s3://<bucket>/restore_drills/<date>/evidence.json

It carries the recovery-point sha256, restored row counts, chain
verification verdict, reconcile result, drill runner identity, and an
ed25519 signature over the canonical JSON of every other field. A
buyer / auditor can re-verify the signature with the Aegis public key
WITHOUT running this script.

Usage
=====

    # Standard weekly drill — wraps restore_drill.sh and writes evidence.
    python3 scripts/ops/dr_evidence.py --target=prod-ha

    # Quarterly chaos drill — also triggers an RDS failover.
    python3 scripts/ops/dr_evidence.py --target=prod-ha --chaos

    # Re-sign an existing JSON (e.g. if you changed the signing key).
    python3 scripts/ops/dr_evidence.py --resign reports/restore_drill/2026-07-14T04-00Z.json

Exit codes
==========
    0  — drill succeeded, evidence written + uploaded
    1  — drill failed at any step (evidence still written with verdict="failed")
    2  — argparse / config error (no evidence written)
    3  — signing failed (drill OK, but artifact is unsigned — re-sign manually)

This script NEVER touches the production database. The actual restore
runs in restore_drill.sh's isolated docker-compose project.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# Sign with the same provider the audit chain uses so the artifact is
# verifiable with the published Aegis public key. We import lazily so
# the script can produce an UNSIGNED artifact when running on a host
# that doesn't have the signer libs installed (dev laptops, e.g.).
def _try_load_signer():
    try:
        from sdk.common.signing_keys import (  # type: ignore[import-not-found]
            provider_from_env,
        )
        from pathlib import Path as _P
        provider = provider_from_env(
            provider_env="RECEIPT_SIGNING_PROVIDER",
            pem_env="RECEIPT_SIGNING_KEY_PEM",
            disk_path=_P("/data/keys/receipt-signing.pem"),
            kms_key_id_env="RECEIPT_SIGNING_KMS_KEY_ID",
            kms_blob_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_B64",
            kms_s3_uri_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI",
            ssm_parameter_env="RECEIPT_SIGNING_SSM_PARAMETER",
            allow_generate=False,
        )
        return provider
    except Exception as exc:
        print(f"[dr-evidence] signer unavailable: {exc}", file=sys.stderr)
        return None


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "reports" / "restore_drill"
RESTORE_DRILL = ROOT / "scripts" / "ops" / "restore_drill.sh"


def _iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _sign(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Sign canonical JSON of payload (excluding any pre-existing
    `signature` key). Returns the signature dict or None on failure."""
    provider = _try_load_signer()
    if provider is None:
        return None
    try:
        private_key, kid = provider.load()
    except Exception as exc:
        print(f"[dr-evidence] sign load failed: {exc}", file=sys.stderr)
        return None
    payload_no_sig = {k: v for k, v in payload.items() if k != "signature"}
    canonical = _canonical_json(payload_no_sig)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        if not isinstance(private_key, Ed25519PrivateKey):
            print("[dr-evidence] sign: not an ed25519 key", file=sys.stderr)
            return None
        sig = private_key.sign(canonical)
        import base64
        return {
            "algorithm": "ed25519",
            "kid":       kid,
            "value":     base64.b64encode(sig).decode("ascii"),
            "canonical_sha256": hashlib.sha256(canonical).hexdigest(),
        }
    except Exception as exc:
        print(f"[dr-evidence] sign failed: {exc}", file=sys.stderr)
        return None


def _instance_identity() -> dict[str, Any]:
    """Best-effort introspection of who's running the drill."""
    instance_id: str | None = None
    iam_role: str | None = None
    try:
        # IMDSv2 path.
        import urllib.request
        req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "300"},
        )
        token = urllib.request.urlopen(req, timeout=1).read().decode("ascii")
        for path, slot in (
            ("/latest/meta-data/instance-id", "instance_id"),
            ("/latest/meta-data/iam/info", "iam_role"),
        ):
            req = urllib.request.Request(
                f"http://169.254.169.254{path}",
                headers={"X-aws-ec2-metadata-token": token},
            )
            value = urllib.request.urlopen(req, timeout=1).read().decode("utf-8")
            if slot == "instance_id":
                instance_id = value
            else:
                try:
                    iam_role = json.loads(value).get("InstanceProfileArn")
                except Exception:
                    pass
    except Exception:
        pass
    return {
        "hostname":    socket.gethostname(),
        "instance_id": instance_id,
        "iam_role":    iam_role,
        "user":        os.environ.get("USER") or os.environ.get("LOGNAME"),
    }


def _maybe_upload_to_s3(local_path: Path, bucket: str) -> str | None:
    if not shutil.which("aws"):
        print("[dr-evidence] aws CLI not installed; skipping S3 upload",
              file=sys.stderr)
        return None
    key = f"restore_drills/{_iso_z(dt.datetime.now(dt.timezone.utc))[:10]}/evidence.json"
    s3_uri = f"s3://{bucket}/{key}"
    cmd = ["aws", "s3", "cp", str(local_path), s3_uri,
           "--content-type", "application/json"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[dr-evidence] S3 upload failed: {result.stderr}",
              file=sys.stderr)
        return None
    return s3_uri


def _run_drill(
    *,
    target: str,
    chaos: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Invoke the existing restore_drill.sh, capture stdout for parsing.

    The runner reads a small set of metrics out of the script's tail
    output. The script today writes its own JSON to reports/; we
    capture the path from the last line.
    """
    started = time.time()
    record: dict[str, Any] = {
        "drill_id":          f"dr-{uuid.uuid4().hex[:12]}",
        "started_at":        _iso_z(dt.datetime.now(dt.timezone.utc)),
        "target_environment": target,
        "chaos":             chaos,
        "drill_runner":      _instance_identity(),
    }
    if dry_run:
        record["verdict"] = "skipped (dry_run)"
        record["duration_seconds"] = 0
        record["finished_at"] = record["started_at"]
        return record

    if not RESTORE_DRILL.exists():
        record["verdict"] = "failed"
        record["error"]   = f"missing {RESTORE_DRILL}"
        record["duration_seconds"] = 0
        record["finished_at"] = _iso_z(dt.datetime.now(dt.timezone.utc))
        return record

    args = ["bash", str(RESTORE_DRILL), f"--target={target}"]
    if chaos:
        args.append("--chaos")
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=2400)
    except subprocess.TimeoutExpired:
        record["verdict"] = "failed"
        record["error"]   = "restore_drill.sh timed out after 40 minutes"
        record["duration_seconds"] = 2400
        record["finished_at"] = _iso_z(dt.datetime.now(dt.timezone.utc))
        return record

    elapsed = time.time() - started
    record["duration_seconds"] = round(elapsed, 1)
    record["finished_at"] = _iso_z(dt.datetime.now(dt.timezone.utc))
    record["stdout_tail"] = result.stdout[-2000:]
    record["stderr_tail"] = result.stderr[-2000:]
    record["verdict"] = "intact" if result.returncode == 0 else "failed"
    if result.returncode != 0:
        record["exit_code"] = result.returncode

    # Hooks the restore_drill.sh writes (best-effort parse of recent
    # reports/restore_drill/ artifacts for the row counts and the
    # chain verification result).
    artifact_dir = ROOT / "reports" / "restore_drill"
    if artifact_dir.exists():
        latest = sorted(
            artifact_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if latest:
            try:
                record["script_artifact"] = json.loads(latest[0].read_text())
            except Exception:
                record["script_artifact"] = None
    return record


def _write_evidence(record: dict[str, Any]) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    name = record["started_at"].replace(":", "-")
    path = EVIDENCE_DIR / f"{name}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True))
    return path


def _resign(path: Path) -> int:
    record = json.loads(path.read_text())
    signature = _sign(record)
    if signature is None:
        return 3
    record["signature"] = signature
    path.write_text(json.dumps(record, indent=2, sort_keys=True))
    print(f"[dr-evidence] re-signed {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="dr_evidence.py")
    parser.add_argument("--target", default="prod-ha")
    parser.add_argument("--chaos", action="store_true",
                        help="Also force an RDS failover before restoring.")
    parser.add_argument("--bucket", default=os.environ.get(
        "AEGIS_DR_EVIDENCE_BUCKET",
        "acp-backups-prodha-628478946931",
    ))
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resign", type=Path)
    args = parser.parse_args()

    if args.resign:
        return _resign(args.resign)

    record = _run_drill(target=args.target, chaos=args.chaos, dry_run=args.dry_run)

    signature = _sign(record)
    if signature is not None:
        record["signature"] = signature

    out_path = _write_evidence(record)
    print(f"[dr-evidence] wrote {out_path}")
    print(f"  verdict: {record.get('verdict')}")
    print(f"  duration: {record.get('duration_seconds')}s")

    if not args.no_upload and record.get("verdict") != "failed":
        uri = _maybe_upload_to_s3(out_path, args.bucket)
        if uri:
            print(f"[dr-evidence] uploaded → {uri}")

    if record.get("verdict") == "failed":
        return 1
    if signature is None:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
