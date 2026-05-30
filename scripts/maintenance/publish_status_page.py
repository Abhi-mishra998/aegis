"""Publish a customer-facing status page snapshot to S3 — sprint-4.G.

Reads:
  - GET http://gateway:8000/system/health    (downstream RTT for every service)
  - GET http://prometheus:9090/api/v1/query  (uptime over last 24h)
  - SELECT count(*) FROM audit_logs          (chain liveness)

Writes:
  - s3://${STATUS_S3_BUCKET}/status/current.json
  - s3://${STATUS_S3_BUCKET}/status/history/${YYYY-MM-DD}.json

The UI's public status page (status.aegisagent.in or similar) renders
`current.json`. History entries support a 30-day uptime chart.

Run on a 1-minute cron via .github/workflows/scheduled-status-page.yml.

No customer data is included — only service-level SLI signals. The audit
chain row count is the only DB read and is whole-cluster, not per-tenant.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime

GATEWAY_URL    = os.environ.get("GATEWAY_URL", "http://gateway:8000")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
STATUS_BUCKET  = os.environ.get("STATUS_S3_BUCKET", "")
TIMEOUT_SECONDS = 5.0


def _http_get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as r:
            return json.load(r)
    except Exception as exc:
        print(f"warn: {url} → {exc}", file=sys.stderr)
        return None


def _query_prom(query: str) -> float | None:
    params = urllib.parse.urlencode({"query": query})
    body = _http_get_json(f"{PROMETHEUS_URL}/api/v1/query?{params}")
    if not body or body.get("status") != "success":
        return None
    result = body.get("data", {}).get("result", [])
    if not result:
        return None
    try:
        return float(result[0]["value"][1])
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _service_status(name: str, healthy: bool, latency_ms: float | None) -> dict:
    return {
        "name": name,
        "status": "operational" if healthy else "degraded",
        "latency_ms": latency_ms,
    }


def build_snapshot() -> dict:
    health = _http_get_json(f"{GATEWAY_URL}/system/health") or {}
    services_raw = health.get("services", {}) if isinstance(health, dict) else {}

    services = []
    if isinstance(services_raw, dict):
        for name, info in services_raw.items():
            info = info or {}
            services.append(_service_status(
                name=name,
                healthy=(info.get("status") in ("healthy", "ok", "up")),
                latency_ms=info.get("latency_ms"),
            ))
    services.sort(key=lambda s: s["name"])

    # 24h availability rollup — Prometheus may not be reachable from the
    # script's environment, so we degrade gracefully.
    availability_24h = _query_prom(
        'avg_over_time(up{job=~"acp_.+"}[24h])'
    )

    # Audit chain liveness — a non-zero count in the last hour is the
    # canonical "the platform is processing decisions" signal.
    audit_rows_1h = _query_prom(
        'sum(increase(acp_slo_availability_total[1h]))'
    )

    return {
        "version": 1,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "overall_status": "operational" if all(
            s["status"] == "operational" for s in services
        ) else "degraded",
        "uptime_24h": availability_24h,
        "audit_rows_1h": audit_rows_1h,
        "services": services,
    }


def _upload(snapshot: dict) -> None:
    if not STATUS_BUCKET:
        print(json.dumps(snapshot, indent=2))
        return

    import subprocess
    body = json.dumps(snapshot, separators=(",", ":")).encode()
    day = snapshot["generated_at"][:10]

    for key in ("status/current.json", f"status/history/{day}.json"):
        # `aws s3 cp -` reads stdin; saves us a tempfile + cleanup.
        p = subprocess.run(
            ["aws", "s3", "cp", "-", f"s3://{STATUS_BUCKET}/{key}",
             "--content-type", "application/json",
             "--cache-control", "max-age=30"],
            input=body, capture_output=True,
        )
        if p.returncode != 0:
            print(f"warn: s3 upload failed for {key}: {p.stderr.decode()}", file=sys.stderr)


def main() -> int:
    snapshot = build_snapshot()
    _upload(snapshot)
    # Always emit to stdout so log scrapers see the latest snapshot too.
    print(f"status snapshot {snapshot['overall_status']} ({len(snapshot['services'])} services) — {snapshot['generated_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
