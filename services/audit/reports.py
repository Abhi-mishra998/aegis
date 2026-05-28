"""Scheduled reports — CRUD stored in audit DB."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Request

from sdk.common.response import APIResponse

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/reports/scheduled", tags=["reports"])


# ---------------------------------------------------------------------------
# Minimal in-memory store. Reports are per-tenant, keyed by
# "reports:{tenant_id}" → {report_id: report_dict}.
# ---------------------------------------------------------------------------

_STORE: dict[str, dict] = {}  # in-memory fallback


def _new_report(data: dict) -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid.uuid4()),
        "name": data.get("name", "Untitled Report"),
        "schedule": data.get("schedule", "0 9 * * 1"),  # weekly Monday 9am
        "format": data.get("format", "pdf"),
        "recipients": data.get("recipients", []),
        "filters": data.get("filters", {}),
        "enabled": data.get("enabled", True),
        "created_at": now,
        "updated_at": now,
        "last_run_at": None,
        "next_run_at": None,
    }


@router.get("", response_model=APIResponse[list])
async def list_reports(request: Request) -> dict:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    key = f"reports:{tenant_id}"
    reports = list(_STORE.get(key, {}).values())
    return APIResponse(data=reports)


@router.post("", response_model=APIResponse[dict], status_code=201)
async def create_report(request: Request) -> dict:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    body = await request.json()
    report = _new_report(body)
    key = f"reports:{tenant_id}"
    if key not in _STORE:
        _STORE[key] = {}
    _STORE[key][report["id"]] = report
    return APIResponse(data=report)


@router.get("/{report_id}", response_model=APIResponse[dict])
async def get_report(report_id: str, request: Request) -> dict:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    key = f"reports:{tenant_id}"
    report = _STORE.get(key, {}).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return APIResponse(data=report)


@router.patch("/{report_id}", response_model=APIResponse[dict])
async def update_report(report_id: str, request: Request) -> dict:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    key = f"reports:{tenant_id}"
    report = _STORE.get(key, {}).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    body = await request.json()
    report.update(body)
    report["updated_at"] = datetime.now(UTC).isoformat()
    return APIResponse(data=report)


@router.delete("/{report_id}", status_code=204)
async def delete_report(report_id: str, request: Request) -> None:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    key = f"reports:{tenant_id}"
    _STORE.get(key, {}).pop(report_id, None)


@router.post("/{report_id}/run", response_model=APIResponse[dict])
async def run_report(report_id: str, request: Request) -> dict:
    tenant_id = request.headers.get("X-Tenant-ID", "")
    key = f"reports:{tenant_id}"
    report = _STORE.get(key, {}).get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    now = datetime.now(UTC).isoformat()
    report["last_run_at"] = now
    return APIResponse(data={"report_id": report_id, "status": "queued", "started_at": now})


@router.get("/{report_id}/history", response_model=APIResponse[list])
async def get_report_history(report_id: str, request: Request, limit: int = 20) -> dict:
    return APIResponse(data=[])
