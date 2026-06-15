#!/usr/bin/env python3
"""curl-driven latency probe — same scope as latency_probe.py but uses
curl via subprocess to bypass an httpx-on-Python-3.14 issue this machine
is hitting on prod-ha. The probe itself measures the same /execute path."""
from __future__ import annotations
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

BASE = os.environ.get("BASE", "https://ha.aegisagent.in")
TENANT = os.environ.get("TENANT", "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL = os.environ.get("ACP_ADMIN_EMAIL", "admin@acp.local")
ADMIN_PASSWORD = os.environ.get("ACP_ADMIN_PASSWORD", "admin1234")
DURATION_S = int(os.environ.get("DURATION_S", "60"))
WORKERS = int(os.environ.get("WORKERS", "5"))
INTERVAL_S = float(os.environ.get("INTERVAL_S", "1.0"))


def _post(url: str, headers: dict, body: dict | None, timeout: int = 20) -> tuple[int, str, float]:
    """Write body to a temp file, status code goes to stdout via -w."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        body_path = f.name
        if body is not None:
            f.write(json.dumps(body))
    args = ["curl", "-sS", "-X", "POST", "-m", str(timeout),
            "-w", "%{http_code}", "-o", "/dev/null"]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    if body is not None:
        args += ["--data", f"@{body_path}"]
    args.append(url)
    t0 = time.perf_counter()
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return -1, "subprocess_timeout", (time.perf_counter() - t0) * 1000
    finally:
        try:
            os.unlink(body_path)
        except FileNotFoundError:
            pass
    elapsed = time.perf_counter() - t0
    try:
        code = int(out.stdout.strip())
    except (ValueError, AttributeError):
        code = -1
    return code, out.stderr, elapsed * 1000


def _post_with_body(url: str, headers: dict, body: dict | None, timeout: int = 20) -> tuple[int, str, float]:
    """Like _post but also captures response body. Used for provisioning."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        in_path = f.name
        if body is not None:
            f.write(json.dumps(body))
    with tempfile.NamedTemporaryFile(suffix=".out", delete=False) as f:
        out_path = f.name
    args = ["curl", "-sS", "-X", "POST", "-m", str(timeout),
            "-w", "%{http_code}", "-o", out_path]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    if body is not None:
        args += ["--data", f"@{in_path}"]
    args.append(url)
    t0 = time.perf_counter()
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5)
    finally:
        try:
            os.unlink(in_path)
        except FileNotFoundError:
            pass
    elapsed = time.perf_counter() - t0
    try:
        code = int(out.stdout.strip())
    except (ValueError, AttributeError):
        code = -1
    try:
        with open(out_path) as f:
            body_text = f.read()
    finally:
        try:
            os.unlink(out_path)
        except FileNotFoundError:
            pass
    return code, body_text, elapsed * 1000


def admin_token() -> str:
    code, body, _ = _post_with_body(f"{BASE}/auth/token",
        {"Content-Type": "application/json", "X-Tenant-ID": TENANT},
        {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    if code != 200:
        raise RuntimeError(f"auth failed: HTTP {code} body={body[:200]}")
    return json.loads(body)["data"]["access_token"]


def provision(token: str) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    code, body, _ = _post_with_body(f"{BASE}/agents", h, {
        "name": f"latprobe-{uuid.uuid4().hex[:6]}",
        "description": "latency probe agent (curl)",
        "risk_level": "low",
    }, timeout=15)
    if code not in (200, 201):
        raise RuntimeError(f"provision agent failed: HTTP {code} body={body[:200]}")
    agent_id = json.loads(body)["data"]["id"]
    code, body, _ = _post_with_body(f"{BASE}/api-keys", h, {
        "name": "latprobe", "agent_id": agent_id, "ttl_seconds": 7200,
    }, timeout=15)
    if code not in (200, 201):
        raise RuntimeError(f"key failed: HTTP {code} body={body[:200]}")
    key = json.loads(body)["data"]["api_key"]
    _post_with_body(f"{BASE}/agents/{agent_id}/permissions", h,
          {"tool_name": "tool.sql_query", "action": "ALLOW"}, timeout=15)
    return agent_id, key


def worker(agent_id: str, key: str, deadline: float,
           latencies: list[float], errors: list[str]) -> None:
    headers = {"Authorization": f"Bearer {key}",
               "X-Tenant-ID": TENANT, "X-Agent-ID": agent_id,
               "Content-Type": "application/json"}
    body = {"agent_id": agent_id, "tool": "tool.sql_query",
            "arguments": {"query": "SELECT 1", "row_limit": 1}}
    while time.monotonic() < deadline:
        code, _resp_body, elapsed_ms = _post(f"{BASE}/execute", headers, body, timeout=15)
        if code == 200:
            latencies.append(elapsed_ms)
        elif code == -1:
            errors.append("curl_error")
        else:
            errors.append(f"HTTP_{code}")
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
        t.join(timeout=DURATION_S + 30)

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
    for k, v in summary.items():
        print(f"  {k:14s} = {v}")
    out = Path(__file__).resolve().parents[2] / "reports" / "latency-2026-06-14.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\n✓ → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
