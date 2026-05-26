"""Push audit events to Splunk HEC or Datadog Logs."""
import json
import os

import httpx


async def push_to_splunk(events: list[dict], hec_url: str = "", token: str = "") -> dict:
    """POST events to Splunk HTTP Event Collector. Returns {sent, failed, status}."""
    url = hec_url or os.environ.get("SPLUNK_HEC_URL", "")
    tok = token or os.environ.get("SPLUNK_HEC_TOKEN", "")
    if not url or not tok:
        return {"status": "skipped", "reason": "no Splunk HEC configured"}
    batch = "\n".join(json.dumps({"event": e, "sourcetype": "acp:audit"}) for e in events)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(url, content=batch, headers={"Authorization": f"Splunk {tok}", "Content-Type": "application/json"})
        return {"status": "sent" if r.status_code in (200, 201) else "error", "http_status": r.status_code, "sent": len(events)}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}

async def push_to_datadog(events: list[dict], api_key: str = "", site: str = "datadoghq.com") -> dict:
    """POST events to Datadog Logs Intake API."""
    key = api_key or os.environ.get("DATADOG_API_KEY", "")
    if not key:
        return {"status": "skipped", "reason": "no Datadog API key configured"}
    logs = [{"message": json.dumps(e), "ddsource": "acp", "ddtags": "service:aegis", "service": "acp-audit"} for e in events]
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"https://http-intake.logs.{site}/api/v2/logs",
                             json=logs, headers={"DD-API-KEY": key, "Content-Type": "application/json"})
        return {"status": "sent" if r.status_code == 202 else "error", "http_status": r.status_code, "sent": len(events)}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
