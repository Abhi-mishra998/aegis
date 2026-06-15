#!/usr/bin/env python3
"""
Real-traffic latency probe for /execute on prod-ha — synchronous variant.

Drives the allow path (small benign SELECT) through the full /execute
pipeline (auth → middleware extraction → decision fan-out → policy fast-
path → behavior consult → finalise → audit emit) and captures per-request
wall-clock latency.

Scale envelope: prod-ha enforces a per-(tenant, jti) sliding window of
30 requests in 10 seconds, plus the runaway-loop auto-quarantine at 50
failures / 5 min. This probe runs 5 worker threads at 1 req/s for 60s —
5 rps sustained, ~300 samples. Above that we'd need to widen the
sliding window OR pre-provision an agent pool.

A 1k/10k concurrent probe needs (a) a pool of ~30 long-lived agents
stored in Redis so we don't re-provision every run, (b) a tenant-level
override that disables the sliding window for the load-test tenant, and
(c) Locust to drive distributed workers. Tracked for the next sprint.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx

BASE = os.environ.get("BASE", "https://ha.aegisagent.in")
TENANT = os.environ.get("TENANT", "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL = os.environ.get("ACP_ADMIN_EMAIL", "admin@acp.local")
ADMIN_PASSWORD = os.environ.get("ACP_ADMIN_PASSWORD", "admin1234")
DURATION_S = int(os.environ.get("DURATION_S", "60"))
WORKERS = int(os.environ.get("WORKERS", "5"))
INTERVAL_S = float(os.environ.get("INTERVAL_S", "1.0"))


def admin_token() -> str:
    r = httpx.post(
        f"{BASE}/auth/token",
        headers={"Content-Type": "application/json", "X-Tenant-ID": TENANT},
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["data"]["access_token"]


def provision(token: str) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    r = httpx.post(f"{BASE}/agents", headers=h, json={
        "name": f"latprobe-{uuid.uuid4().hex[:6]}",
        "description": "latency probe agent",
        "risk_level": "low",
    }, timeout=30)
    r.raise_for_status()
    agent_id = r.json()["data"]["id"]
    r = httpx.post(f"{BASE}/api-keys", headers=h, json={
        "name": "latprobe", "agent_id": agent_id, "ttl_seconds": 7200,
    }, timeout=30)
    r.raise_for_status()
    key = r.json()["data"]["api_key"]
    httpx.post(f"{BASE}/agents/{agent_id}/permissions", headers=h,
        json={"tool_name": "tool.sql_query", "action": "ALLOW"},
        timeout=30)
    return agent_id, key


def worker(agent_id: str, key: str, deadline: float,
           latencies: list[float], errors: list[str]) -> None:
    headers = {"Authorization": f"Bearer {key}",
               "X-Tenant-ID": TENANT, "X-Agent-ID": agent_id,
               "Content-Type": "application/json"}
    body = {"agent_id": agent_id, "tool": "tool.sql_query",
            "arguments": {"query": "SELECT 1", "row_limit": 1}}
    with httpx.Client(timeout=15) as c:
        while time.monotonic() < deadline:
            t0 = time.perf_counter()
            try:
                r = c.post(f"{BASE}/execute", headers=headers, json=body)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                if r.status_code == 200:
                    latencies.append(elapsed_ms)
                else:
                    errors.append(f"HTTP_{r.status_code}")
            except Exception as exc:
                errors.append(type(exc).__name__)
            time.sleep(INTERVAL_S)


def main() -> int:
    token = admin_token()
    agent_id, key = provision(token)
    print(f"agent {agent_id}  key {key[:14]}…")
    print(f"workers={WORKERS}  interval={INTERVAL_S}s  duration={DURATION_S}s")
    print(f"target rps ≈ {WORKERS / INTERVAL_S:.1f}")

    latencies: list[float] = []
    errors: list[str] = []
    deadline = time.monotonic() + DURATION_S
    threads = [threading.Thread(target=worker,
                                args=(agent_id, key, deadline, latencies, errors),
                                daemon=True)
               for _ in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=DURATION_S + 5)

    total = len(latencies) + len(errors)
    if not latencies:
        print(f"FATAL: 0 successes; errors={errors[:5]}", file=sys.stderr)
        return 2

    latencies.sort()
    p50 = statistics.median(latencies)
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) >= 100 else latencies[-1]

    summary = {
        "generated_at":  int(time.time()),
        "base":          BASE,
        "workers":       WORKERS,
        "interval_s":    INTERVAL_S,
        "duration_s":    DURATION_S,
        "total":         total,
        "ok":            len(latencies),
        "err":           len(errors),
        "err_rate_pct":  round(len(errors) / total * 100, 2),
        "p50_ms":        round(p50, 1),
        "p95_ms":        round(p95, 1),
        "p99_ms":        round(p99, 1),
        "max_ms":        round(latencies[-1], 1),
        "min_ms":        round(latencies[0], 1),
        "rps":           round(len(latencies) / DURATION_S, 1),
        "err_sample":    errors[:5],
    }

    print()
    for k, v in summary.items():
        print(f"  {k:14s} = {v}")

    out = Path(__file__).resolve().parents[2] / "reports" / "latency-2026-06-14.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n✓ → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
