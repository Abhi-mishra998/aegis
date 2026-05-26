"""
SIEM Integration — Source-contract tests (Phase 4)
====================================================
8 tests that verify the SIEM integration wiring without starting a server
or making real HTTP calls.

Run:
    python3 -m pytest tests/test_siem_integration.py -v
"""

from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. siem_export module exists
# ---------------------------------------------------------------------------


def test_siem_export_module_exists():
    base = Path(__file__).parent.parent
    siem_export_path = base / "services" / "audit" / "siem_export.py"
    assert siem_export_path.exists(), f"Expected {siem_export_path} to exist"


# ---------------------------------------------------------------------------
# 2. push_to_splunk function is defined
# ---------------------------------------------------------------------------


def test_siem_export_has_push_to_splunk():
    from services.audit.siem_export import push_to_splunk  # noqa: PLC0415

    assert callable(push_to_splunk)
    assert inspect.iscoroutinefunction(push_to_splunk)


# ---------------------------------------------------------------------------
# 3. push_to_datadog function is defined
# ---------------------------------------------------------------------------


def test_siem_export_has_push_to_datadog():
    from services.audit.siem_export import push_to_datadog  # noqa: PLC0415

    assert callable(push_to_datadog)
    assert inspect.iscoroutinefunction(push_to_datadog)


# ---------------------------------------------------------------------------
# 4. push_to_splunk returns {status: "skipped"} when no config supplied
# ---------------------------------------------------------------------------


def test_push_to_splunk_skips_when_no_config():
    # Ensure env vars are absent so the function reads empty strings
    env_backup = os.environ.pop("SPLUNK_HEC_URL", None), os.environ.pop("SPLUNK_HEC_TOKEN", None)
    try:
        from services.audit.siem_export import push_to_splunk  # noqa: PLC0415

        result = asyncio.run(push_to_splunk([], hec_url="", token=""))
    finally:
        if env_backup[0] is not None:
            os.environ["SPLUNK_HEC_URL"] = env_backup[0]
        if env_backup[1] is not None:
            os.environ["SPLUNK_HEC_TOKEN"] = env_backup[1]

    assert result["status"] == "skipped", f"Expected 'skipped', got: {result}"


# ---------------------------------------------------------------------------
# 5. push_to_datadog returns {status: "skipped"} when no config supplied
# ---------------------------------------------------------------------------


def test_push_to_datadog_skips_when_no_config():
    env_backup = os.environ.pop("DATADOG_API_KEY", None)
    try:
        from services.audit.siem_export import push_to_datadog  # noqa: PLC0415

        result = asyncio.run(push_to_datadog([], api_key=""))
    finally:
        if env_backup is not None:
            os.environ["DATADOG_API_KEY"] = env_backup

    assert result["status"] == "skipped", f"Expected 'skipped', got: {result}"


# ---------------------------------------------------------------------------
# 6. compliance.py contains SIEM endpoints
# ---------------------------------------------------------------------------


def test_compliance_router_has_siem_endpoints():
    base = Path(__file__).parent.parent
    compliance_path = base / "services" / "audit" / "compliance.py"
    assert compliance_path.exists(), "compliance.py not found"
    content = compliance_path.read_text(encoding="utf-8")
    assert "/siem/config" in content, "Expected /siem/config route in compliance.py"


# ---------------------------------------------------------------------------
# 7. gateway/main.py contains SIEM proxy routes
# ---------------------------------------------------------------------------


def test_gateway_proxies_siem_routes():
    base = Path(__file__).parent.parent
    gateway_path = base / "services" / "gateway" / "main.py"
    assert gateway_path.exists(), "gateway/main.py not found"
    content = gateway_path.read_text(encoding="utf-8")
    assert "/siem/config" in content, "Expected /siem/config proxy in gateway/main.py"


# ---------------------------------------------------------------------------
# 8. api.js exposes siemService
# ---------------------------------------------------------------------------


def test_api_js_has_siem_service():
    base = Path(__file__).parent.parent
    api_js_path = base / "ui" / "src" / "services" / "api.js"
    assert api_js_path.exists(), "api.js not found"
    content = api_js_path.read_text(encoding="utf-8")
    assert "siemService" in content, "Expected siemService export in api.js"
