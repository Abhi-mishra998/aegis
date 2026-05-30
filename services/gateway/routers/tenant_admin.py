"""Gateway admin routes for tenant data export + erasure — sprint-5.2.

GDPR Article 17 (right-to-erasure) and Article 20 (right-to-portability)
are both implemented via existing operator scripts:
  - scripts/ops/export_tenant.py     → builds the portability TAR archive
  - scripts/ops/redact_tenant_pii.py → audit-safe erasure pattern

This router exposes those scripts as authenticated admin HTTP endpoints
so customer-data-subject requests can be served from the UI without
requiring SSH to the EC2 host.

Both routes require ADMIN role at the gateway (sprint-1 enforcement).
The execution runs in a subprocess so the gateway worker is not blocked
on the multi-minute export/redact job; the route returns a job_id and
the UI polls /admin/tenants/{tenant_id}/jobs/{job_id} for completion.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid as _uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway._helpers import require_admin_role

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["tenant-admin"])

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXPORT_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "export_tenant.py"
_REDACT_SCRIPT = _REPO_ROOT / "scripts" / "ops" / "redact_tenant_pii.py"

# Job state lives in Redis with a 24-hour TTL — long enough for customer
# polling, short enough to bound storage. Job stdout/stderr are captured
# for the operator audit trail.
_JOB_KEY = "acp:tenant_admin:job:{job_id}"
_JOB_TTL_SECONDS = 24 * 60 * 60

_redis = get_redis_client(settings.REDIS_URL, decode_responses=True)


def _job_key(job_id: str) -> str:
    return _JOB_KEY.format(job_id=job_id)


async def _set_job(job_id: str, state: dict[str, Any]) -> None:
    await _redis.setex(_job_key(job_id), _JOB_TTL_SECONDS, json.dumps(state))


async def _run_subprocess(job_id: str, cmd: list[str], env_extra: dict[str, str] | None = None) -> None:
    """Run a script as a subprocess, stream output into the job record."""
    started = time.time()
    await _set_job(job_id, {
        "status": "running",
        "cmd": cmd,
        "started_at": started,
    })
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        await _set_job(job_id, {
            "status": "completed" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "started_at": started,
            "finished_at": time.time(),
            "stdout_tail": stdout.decode(errors="replace")[-4000:],
            "stderr_tail": stderr.decode(errors="replace")[-4000:],
            "cmd": cmd,
        })
        if proc.returncode != 0:
            logger.error("tenant_admin_job_failed", job_id=job_id, exit_code=proc.returncode)
        else:
            logger.info("tenant_admin_job_completed", job_id=job_id, duration_s=round(time.time() - started, 1))
    except Exception as exc:
        await _set_job(job_id, {
            "status": "failed",
            "started_at": started,
            "finished_at": time.time(),
            "error": str(exc)[:1000],
            "cmd": cmd,
        })
        logger.error("tenant_admin_job_exception", job_id=job_id, error=str(exc))


def _validate_tenant_uuid(tenant_id: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="tenant_id must be a valid UUID") from exc


@router.post("/admin/tenants/{tenant_id}/export")
async def start_tenant_export(
    tenant_id: str,
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    """Kick off a GDPR right-to-portability export for one tenant.

    Returns a job_id immediately. Poll GET /admin/tenants/{tenant_id}/jobs/{job_id}
    for completion. On success the TAR archive path is in the job record;
    GET /admin/tenants/{tenant_id}/exports/{job_id}/download streams it.
    """
    require_admin_role(request)
    tid = _validate_tenant_uuid(tenant_id)

    job_id = str(_uuid.uuid4())
    out_dir = Path(tempfile.gettempdir()) / "aegis-exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / f"{tid}-{job_id}.tar.gz"

    cmd = [
        sys.executable,
        str(_EXPORT_SCRIPT),
        "--tenant-id", str(tid),
        "--output", str(archive_path),
    ]
    await _set_job(job_id, {
        "status": "queued",
        "tenant_id": str(tid),
        "archive_path": str(archive_path),
        "kind": "export",
    })
    background.add_task(_run_subprocess, job_id, cmd)

    logger.info("tenant_export_queued", job_id=job_id, tenant_id=str(tid),
                actor=getattr(request.state, "actor", "unknown"))
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "tenant_id": str(tid),
            "status": "queued",
            "poll_url": f"/admin/tenants/{tid}/jobs/{job_id}",
            "download_url": f"/admin/tenants/{tid}/exports/{job_id}/download",
        },
    )


@router.post("/admin/tenants/{tenant_id}/redact")
async def start_tenant_redact(
    tenant_id: str,
    request: Request,
    background: BackgroundTasks,
) -> JSONResponse:
    """Kick off a GDPR right-to-erasure redaction for one tenant.

    The redact script implements the audit-safe pattern: PII fields are
    overwritten with sha256 hashes; audit_logs row hashes stay valid;
    a chain marker row records the redaction event.
    """
    require_admin_role(request)
    tid = _validate_tenant_uuid(tenant_id)

    # Require explicit confirm-token body to prevent accidental erasure.
    # The token is the literal string "I-CONSENT-TO-REDACT-<tenant_id>".
    body = await request.json() if request.headers.get("content-length") not in (None, "0") else {}
    expected = f"I-CONSENT-TO-REDACT-{tid}"
    if not isinstance(body, dict) or body.get("confirm") != expected:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "missing or wrong confirm token",
                "expected_body": {"confirm": expected},
            },
        )

    job_id = str(_uuid.uuid4())
    cmd = [
        sys.executable,
        str(_REDACT_SCRIPT),
        "--tenant-id", str(tid),
        "--actor", getattr(request.state, "actor", "unknown"),
    ]
    await _set_job(job_id, {
        "status": "queued",
        "tenant_id": str(tid),
        "kind": "redact",
    })
    background.add_task(_run_subprocess, job_id, cmd)

    logger.warning(
        "tenant_redact_queued",
        job_id=job_id,
        tenant_id=str(tid),
        actor=getattr(request.state, "actor", "unknown"),
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "tenant_id": str(tid),
            "status": "queued",
            "poll_url": f"/admin/tenants/{tid}/jobs/{job_id}",
        },
    )


@router.get("/admin/tenants/{tenant_id}/jobs/{job_id}")
async def get_tenant_job(tenant_id: str, job_id: str, request: Request) -> JSONResponse:
    """Poll the status of an export/redact job."""
    require_admin_role(request)
    _validate_tenant_uuid(tenant_id)
    try:
        _uuid.UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="job_id must be a valid UUID") from exc

    raw = await _redis.get(_job_key(job_id))
    if not raw:
        raise HTTPException(status_code=404, detail="job not found or expired (24h TTL)")
    state = json.loads(raw)
    # Defense: job must belong to the tenant in the URL.
    if state.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="job not found for this tenant")
    return JSONResponse(content=state)


@router.get("/admin/tenants/{tenant_id}/exports/{job_id}/download")
async def download_tenant_export(tenant_id: str, job_id: str, request: Request) -> FileResponse:
    """Download a completed export's TAR archive."""
    require_admin_role(request)
    _validate_tenant_uuid(tenant_id)
    raw = await _redis.get(_job_key(job_id))
    if not raw:
        raise HTTPException(status_code=404, detail="job not found or expired")
    state = json.loads(raw)
    if state.get("tenant_id") != tenant_id:
        raise HTTPException(status_code=404, detail="job not found for this tenant")
    if state.get("kind") != "export":
        raise HTTPException(status_code=400, detail="job is not an export")
    if state.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"job not ready: status={state.get('status')}")
    path = state.get("archive_path")
    if not path or not Path(path).exists():
        raise HTTPException(status_code=410, detail="archive no longer on disk")
    logger.info(
        "tenant_export_downloaded",
        job_id=job_id,
        tenant_id=tenant_id,
        actor=getattr(request.state, "actor", "unknown"),
    )
    return FileResponse(
        path=path,
        media_type="application/gzip",
        filename=Path(path).name,
    )
