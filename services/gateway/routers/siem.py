"""Sprint S3 (2026-06-19) — Per-vendor SIEM connection test.

The SiemSettings page in the UI was a single paste-form for two
vendors. S3 makes it a vendor-card grid that supports Splunk + Datadog
+ Elastic + Sentinel + Chronicle, with a real "Test Connection" button
that fires a one-row probe event and reports a structured pass/fail.

The router does NOT persist credentials — that's already handled by
services/audit/siem.py's _resolve_siem_credentials() path (SSM / env
var fallback). This endpoint just answers "are these creds valid?"
before the operator commits them.

Returns the canonical shape:
  { "status": "ok" | "error",
    "vendor": "splunk" | "datadog" | "elastic" | "sentinel" | "chronicle",
    "detail": "Connected. Test event accepted.",
    "latency_ms": 142 }
"""
from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Body, HTTPException, Request

from sdk.common.outbound_url_allowlist import (
    OutboundUrlBlocked,
    validate_outbound_url,
)
from services.audit.siem import (
    ChronicleForwarder,
    DatadogForwarder,
    ElasticForwarder,
    SentinelForwarder,
    SIEMEvent,
    SplunkHECForwarder,
)

# SEC-2026-07-31 (H5): allow-list per SIEM vendor. Any URL/host outside
# this list is refused before we make an outbound request — closes the
# blind-SSRF via 169.254.169.254 / private-VPC hosts.
_SIEM_HOST_ALLOWLISTS: dict[str, tuple[str, ...]] = {
    "splunk":   ("splunkcloud.com", "splunk.com"),
    "datadog":  (
        "datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com",
        "datadoghq.eu", "ddog-gov.com", "ap1.datadoghq.com",
    ),
    "elastic":  ("elastic-cloud.com", "found.io", "es.amazonaws.com"),
    "sentinel": ("azure.com", "microsoft.com", "azurewebsites.net"),
    "chronicle": ("googleapis.com", "google.com"),
}


def _host_in_allowlist(host: str, vendor: str) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    allowlist = _SIEM_HOST_ALLOWLISTS.get(vendor, ())
    return any(host == a or host.endswith(f".{a}") for a in allowlist)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/siem", tags=["siem"])


# ── Test event fixture ────────────────────────────────────────────────
def _make_test_event(tenant_id: str) -> SIEMEvent:
    """One synthetic event the test endpoint fires at the target SIEM.

    Marked clearly in `action` + `reason` so a customer's SIEM filter
    can identify Aegis-test rows separately from production events.
    """
    return SIEMEvent(
        timestamp=datetime.now(UTC).isoformat(),
        tenant_id=tenant_id,
        agent_id="00000000-0000-0000-0000-000000000000",
        action="aegis_siem_connection_test",
        tool=None,
        decision="monitor",
        reason="Aegis connection test from /siem/test — safe to drop.",
        risk_score=0.0,
        request_id="aegis-siem-test",
        event_hash=None,
    )


# ── Per-vendor probe ──────────────────────────────────────────────────
async def _probe_splunk(creds: dict) -> dict:
    url = (creds.get("hec_url") or "").strip()
    token = (creds.get("hec_token") or "").strip()
    if not url or not token:
        return _err("splunk", "Both hec_url and hec_token are required.")
    if not url.startswith("https://"):
        return _err("splunk", "hec_url must use https://")
    # SEC-2026-07-31 (H5): reject metadata-service / private-VPC / DNS-rebind
    # targets, then enforce a Splunk-domain allow-list.
    try:
        validate_outbound_url(url)
    except OutboundUrlBlocked as exc:
        return _err("splunk", f"hec_url rejected: {exc}")
    from urllib.parse import urlparse
    if not _host_in_allowlist(urlparse(url).hostname or "", "splunk"):
        return _err("splunk", "hec_url host is not on the Splunk allow-list")
    fw = SplunkHECForwarder(hec_url=url, hec_token=token)
    return await _run_probe("splunk", fw, creds.get("tenant_id", "test"))


async def _probe_datadog(creds: dict) -> dict:
    site = (creds.get("site") or "datadoghq.com").strip()
    api_key = (creds.get("api_key") or "").strip()
    if not api_key:
        return _err("datadog", "api_key is required.")
    # SEC-2026-07-31 (H5): the `site` field is user-controlled and
    # interpolated into the URL; anything that isn't a Datadog domain is
    # rejected before the outbound POST.
    if not _host_in_allowlist(f"http-intake.logs.{site}", "datadog"):
        return _err("datadog", f"site {site!r} is not a known Datadog region")
    logs_url = f"https://http-intake.logs.{site}/api/v2/logs"
    try:
        validate_outbound_url(logs_url)
    except OutboundUrlBlocked as exc:
        return _err("datadog", f"logs_url rejected: {exc}")
    fw = DatadogForwarder(logs_url=logs_url, api_key=api_key)
    return await _run_probe("datadog", fw, creds.get("tenant_id", "test"))


async def _probe_elastic(creds: dict) -> dict:
    cloud_id = (creds.get("cloud_id") or "").strip()
    api_key = (creds.get("api_key") or "").strip()
    index = (creds.get("index") or "aegis-audit").strip()
    if not cloud_id or not api_key:
        return _err("elastic", "Both cloud_id and api_key are required.")
    # SEC-2026-07-31 (H5 extension): ElasticForwarder decodes cloud_id
    # into ``https://<uuid>.<host>``. Validate the reconstructed URL
    # rejects metadata IPs / private CIDRs and enforce an Elastic-
    # domain allow-list (blocks a fake cloud_id pointing at attacker.com).
    try:
        _fw_preview = ElasticForwarder(cloud_id=cloud_id, api_key=api_key, index=index)
        _decoded_url = getattr(_fw_preview, "_cluster_url", "")
    except Exception as _exc:
        return _err("elastic", f"cloud_id malformed: {_exc}")
    try:
        validate_outbound_url(_decoded_url)
    except OutboundUrlBlocked as _exc:
        return _err("elastic", f"cloud_id resolves to a rejected URL: {_exc}")
    from urllib.parse import urlparse as _urlparse
    if not _host_in_allowlist(_urlparse(_decoded_url).hostname or "", "elastic"):
        return _err("elastic", "cloud_id host is not on the Elastic allow-list")
    fw = _fw_preview
    return await _run_probe("elastic", fw, creds.get("tenant_id", "test"))


async def _probe_sentinel(creds: dict) -> dict:
    workspace_id = (creds.get("workspace_id") or "").strip()
    shared_key = (creds.get("shared_key") or "").strip()
    log_type = (creds.get("log_type") or "AegisAudit").strip()
    if not workspace_id or not shared_key:
        return _err("sentinel", "Both workspace_id and shared_key are required.")
    fw = SentinelForwarder(
        workspace_id=workspace_id, shared_key=shared_key, log_type=log_type,
    )
    return await _run_probe("sentinel", fw, creds.get("tenant_id", "test"))


async def _probe_chronicle(creds: dict) -> dict:
    customer_id = (creds.get("customer_id") or "").strip()
    service_account_json = (creds.get("service_account_json") or "").strip()
    if not customer_id or not service_account_json:
        return _err(
            "chronicle",
            "Both customer_id and service_account_json are required.",
        )
    try:
        fw = ChronicleForwarder(
            customer_id=customer_id,
            service_account_json=service_account_json,
        )
    except Exception as exc:  # noqa: BLE001 — surface construction errors
        return _err("chronicle", f"Could not construct forwarder: {exc}")
    return await _run_probe("chronicle", fw, creds.get("tenant_id", "test"))


_VENDORS = {
    "splunk":    _probe_splunk,
    "datadog":   _probe_datadog,
    "elastic":   _probe_elastic,
    "sentinel":  _probe_sentinel,
    "chronicle": _probe_chronicle,
}


# ── Shared probe runner ───────────────────────────────────────────────
async def _run_probe(vendor: str, fw, tenant_id: str) -> dict:
    """Fire one test event, time it, classify outcome.

    The underlying SplunkHECForwarder / DatadogForwarder / etc. swallow
    every exception and return bool — failure mode is not "raise" but
    "return False with a structured log". We pair the bool with a
    latency reading so the operator sees both "it worked" and "how
    fast" — useful when the vendor is reachable but slow.
    """
    start = time.perf_counter()
    try:
        ok = await fw.forward(_make_test_event(tenant_id))
    except Exception as exc:  # noqa: BLE001 — belt + braces
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.warning("siem_test_exception", vendor=vendor, error=str(exc))
        return _err(
            vendor,
            f"Probe raised: {exc}",
            latency_ms=latency_ms,
        )
    latency_ms = int((time.perf_counter() - start) * 1000)
    if ok:
        return {
            "status": "ok",
            "vendor": vendor,
            "detail": f"{vendor} accepted the test event.",
            "latency_ms": latency_ms,
        }
    return _err(
        vendor,
        (
            f"{vendor} rejected the test event. Check vendor-specific gateway "
            f"logs and the credentials you pasted."
        ),
        latency_ms=latency_ms,
    )


def _err(vendor: str, detail: str, *, latency_ms: int | None = None) -> dict:
    out = {"status": "error", "vendor": vendor, "detail": detail}
    if latency_ms is not None:
        out["latency_ms"] = latency_ms
    return out


# ── POST /siem/test ───────────────────────────────────────────────────
@router.post("/test")
async def siem_test(
    request: Request,
    payload: Annotated[dict, Body()],
) -> dict:
    """Test a SIEM connector without persisting credentials.

    Body shape:
      { "vendor": "splunk" | "datadog" | "elastic" | "sentinel" | "chronicle",
        "credentials": { vendor-specific keys } }

    Vendor → required credential keys:
      splunk    → hec_url, hec_token
      datadog   → api_key (optional: site, defaults to "datadoghq.com")
      elastic   → cloud_id, api_key (optional: index, defaults to "aegis-audit")
      sentinel  → workspace_id, shared_key (optional: log_type, default "AegisAudit")
      chronicle → customer_id, service_account_json
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    vendor = (payload.get("vendor") or "").strip().lower()
    if vendor not in _VENDORS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown vendor '{vendor}'. Known: {sorted(_VENDORS)}.",
        )
    creds = dict(payload.get("credentials") or {})
    creds.setdefault("tenant_id", str(tenant_id))

    logger.info("siem_test_start", tenant_id=str(tenant_id), vendor=vendor)
    return await _VENDORS[vendor](creds)


@router.get("/vendors")
async def siem_vendors() -> dict:
    """Static metadata the UI uses to render the vendor card grid."""
    return {
        "vendors": [
            {
                "id": "splunk",
                "label": "Splunk",
                "fields": [
                    {"name": "hec_url",   "label": "HEC URL",   "type": "url",    "placeholder": "https://splunk.example.com:8088/services/collector"},
                    {"name": "hec_token", "label": "HEC Token", "type": "secret", "placeholder": "00000000-0000-0000-0000-000000000000"},
                ],
                "doc_hint": "Settings → Data inputs → HTTP Event Collector → Tokens.",
            },
            {
                "id": "datadog",
                "label": "Datadog",
                "fields": [
                    {"name": "api_key", "label": "API Key", "type": "secret", "placeholder": "Datadog application key"},
                    {"name": "site",    "label": "Site",    "type": "select", "options": ["datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "datadoghq.eu", "ddog-gov.com"], "default": "datadoghq.com"},
                ],
                "doc_hint": "Organization Settings → API Keys.",
            },
            {
                "id": "elastic",
                "label": "Elastic",
                "fields": [
                    {"name": "cloud_id", "label": "Cloud ID", "type": "text",   "placeholder": "<deployment-name>:<base64-encoded-host-info>"},
                    {"name": "api_key",  "label": "API Key",  "type": "secret", "placeholder": "Encoded API key (id:secret as base64)"},
                    {"name": "index",    "label": "Index",    "type": "text",   "default": "aegis-audit"},
                ],
                "doc_hint": "Kibana → Stack Management → API keys; deployment Cloud ID from cloud.elastic.co.",
            },
            {
                "id": "sentinel",
                "label": "Microsoft Sentinel",
                "fields": [
                    {"name": "workspace_id", "label": "Workspace ID", "type": "text",   "placeholder": "Log Analytics Workspace ID (GUID)"},
                    {"name": "shared_key",   "label": "Shared Key",   "type": "secret", "placeholder": "Primary or secondary key from Workspace → Agents"},
                    {"name": "log_type",     "label": "Log Type",     "type": "text",   "default": "AegisAudit"},
                ],
                "doc_hint": "Log Analytics workspace → Agents → Primary key.",
            },
            {
                "id": "chronicle",
                "label": "Google Chronicle",
                "fields": [
                    {"name": "customer_id",         "label": "Customer ID",         "type": "text",   "placeholder": "Chronicle BYOP customer id (UUID)"},
                    {"name": "service_account_json","label": "Service Account JSON","type": "secret", "placeholder": "Paste the full GCP service-account JSON"},
                ],
                "doc_hint": "Chronicle → Settings → SIEM Settings → Customer ID; GCP service account with chronicle.events.write.",
            },
        ],
    }
