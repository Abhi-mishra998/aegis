#!/usr/bin/env python3
"""
Aegis gateway p95 latency harness — Sprint 0.

This is the SINGLE committed source of the latency number quoted in the README
and the GitBook. Running it against the reference deployment produces
`reports/gateway_p95.json` with the measurement, the methodology, and the
exact hardware/workload profile so the number is reproducible.

The audit (AUDIT_REPORT.md) called out that README claimed ~70 ms p95 while the
GitBook claimed ~21 ms, and the prior perf report (`reports/sprint2_perf_after.json`)
had `"after": null` — never measured. Sprint 0 fixes that by:

  - Committing this harness so anyone can re-run it.
  - Quoting ONE number in both docs, attributed to the latest run of this harness.
  - Labelling the measurement environment "reference deployment (single m6g.medium)"
    — never the word "production", per the global honesty constraint.

Usage:
    # 1. Live measurement against a running gateway (the real workflow).
    python scripts/bench/gateway_p95.py \\
        --target https://dev.aegisagent.in \\
        --token "$ACP_TOKEN" \\
        --tenant 00000000-0000-0000-0000-000000000001 \\
        --agent  00000000-0000-0000-0000-000000000099 \\
        --concurrency 8 \\
        --duration-seconds 60 \\
        --warmup-seconds 5 \\
        --output reports/gateway_p95.json

    # 2. Dry-run that exercises the harness against an in-process sleep
    #    so CI can prove the harness itself is healthy without a live stack.
    python scripts/bench/gateway_p95.py --dry-run --output reports/gateway_p95_dry.json

The harness deliberately reports p50, p95, p99 — not just one number. The
README quotes the **p95** because that is what enterprise SLAs are written in.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field

try:
    import httpx
except ImportError:  # dry-run mode is import-safe even without httpx
    httpx = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    """Serializable bench output written to reports/."""

    harness_version: str
    environment_label: str
    mode: str                       # "live" | "dry-run"
    target: str | None
    started_at_utc: str
    duration_seconds: float
    concurrency: int
    warmup_seconds: int
    total_requests: int
    success_count: int
    error_count: int
    error_breakdown: dict[str, int]
    latency_ms: dict[str, float]    # p50/p95/p99/min/max/mean
    hardware: dict[str, str]
    notes: list[str] = field(default_factory=list)


HARNESS_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Quantiles
# ---------------------------------------------------------------------------


def percentile(samples: list[float], q: float) -> float:
    """Nearest-rank percentile. `q` in [0, 100]."""
    if not samples:
        return float("nan")
    samples = sorted(samples)
    rank = max(1, math.ceil(q / 100.0 * len(samples)))
    return samples[rank - 1]


def summarize_latencies(samples_ms: list[float]) -> dict[str, float]:
    if not samples_ms:
        return {"p50": float("nan"), "p95": float("nan"), "p99": float("nan"),
                "min": float("nan"), "max": float("nan"), "mean": float("nan")}
    return {
        "p50":  round(percentile(samples_ms, 50), 2),
        "p95":  round(percentile(samples_ms, 95), 2),
        "p99":  round(percentile(samples_ms, 99), 2),
        "min":  round(min(samples_ms), 2),
        "max":  round(max(samples_ms), 2),
        "mean": round(statistics.fmean(samples_ms), 2),
    }


# ---------------------------------------------------------------------------
# Live-mode worker
# ---------------------------------------------------------------------------


async def _run_live(
    target: str,
    token: str,
    tenant_id: str,
    agent_id: str,
    concurrency: int,
    duration_seconds: float,
    warmup_seconds: float,
) -> tuple[list[float], int, dict[str, int]]:
    """
    Drive `concurrency` parallel workers against `{target}/system/health` for
    `duration_seconds`. We choose `/system/health` because it exercises the
    full 11-stage middleware (auth + tenant resolve + downstream probes) without
    triggering tool execution, matching what the audit observed as the
    "end-to-end" path.
    """
    if httpx is None:
        raise RuntimeError("httpx not installed; install with `pip install httpx`")

    latencies_ms: list[float] = []
    error_breakdown: dict[str, int] = {}
    success_count = 0

    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   tenant_id,
        "X-Agent-ID":    agent_id,
        "Accept":        "application/json",
    }

    stop_at = time.monotonic() + warmup_seconds + duration_seconds
    warmup_until = time.monotonic() + warmup_seconds

    async def worker(client: httpx.AsyncClient) -> None:
        nonlocal success_count
        while time.monotonic() < stop_at:
            t0 = time.perf_counter()
            try:
                resp = await client.get(f"{target.rstrip('/')}/system/health", headers=headers)
                dt = (time.perf_counter() - t0) * 1000.0
                if resp.status_code == 200:
                    if time.monotonic() >= warmup_until:
                        latencies_ms.append(dt)
                        success_count += 1
                else:
                    if time.monotonic() >= warmup_until:
                        error_breakdown[f"http_{resp.status_code}"] = \
                            error_breakdown.get(f"http_{resp.status_code}", 0) + 1
            except Exception as exc:  # noqa: BLE001 — bench-time visibility intentional
                if time.monotonic() >= warmup_until:
                    bucket = type(exc).__name__
                    error_breakdown[bucket] = error_breakdown.get(bucket, 0) + 1

    async with httpx.AsyncClient(timeout=10.0) as client:
        await asyncio.gather(*[worker(client) for _ in range(concurrency)])

    return latencies_ms, success_count, error_breakdown


# ---------------------------------------------------------------------------
# Dry-run mode (in-process; proves the harness is healthy)
# ---------------------------------------------------------------------------


async def _run_dry(concurrency: int, duration_seconds: float) -> tuple[list[float], int, dict[str, int]]:
    """
    Generate a synthetic-but-deterministic latency distribution so CI can
    smoke-test the harness without a live gateway. We sleep, then record the
    sleep duration as a "latency". The output file is clearly labelled
    `mode: dry-run` so it can never be quoted as a real measurement.
    """
    import random

    rng = random.Random(20260612)
    latencies_ms: list[float] = []
    success_count = 0

    stop_at = time.monotonic() + duration_seconds

    async def worker() -> None:
        nonlocal success_count
        while time.monotonic() < stop_at:
            sleep_ms = max(0.5, rng.lognormvariate(2.5, 0.35))
            await asyncio.sleep(sleep_ms / 1000.0)
            latencies_ms.append(sleep_ms)
            success_count += 1

    await asyncio.gather(*[worker() for _ in range(concurrency)])
    return latencies_ms, success_count, {}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _hardware_snapshot() -> dict[str, str]:
    """Best-effort host description so the report says where the number was taken."""
    return {
        "platform":       platform.platform(),
        "machine":        platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count":      str(os.cpu_count() or 0),
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aegis gateway p95 latency harness")
    p.add_argument("--target", help="Gateway base URL (e.g. https://dev.aegisagent.in)")
    p.add_argument("--token", help="ACP bearer token (env ACP_TOKEN as fallback)")
    p.add_argument("--tenant", help="Tenant UUID")
    p.add_argument("--agent",  help="Agent UUID")
    p.add_argument("--concurrency", type=int, default=8,
                   help="Number of parallel workers (default: 8)")
    p.add_argument("--duration-seconds", type=int, default=60,
                   help="Measurement duration excluding warmup (default: 60)")
    p.add_argument("--warmup-seconds", type=int, default=5,
                   help="Warmup duration discarded from results (default: 5)")
    p.add_argument("--environment-label", default="reference deployment (single m6g.medium)",
                   help="Free-form label written into the report (NOT 'production')")
    p.add_argument("--output", default="reports/gateway_p95.json",
                   help="Path to write the JSON report (default: reports/gateway_p95.json)")
    p.add_argument("--dry-run", action="store_true",
                   help="Synthetic in-process run; produces a clearly-labelled smoke-test report")
    p.add_argument("--notes", action="append", default=[],
                   help="Free-form note appended to the report (repeatable)")
    return p.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    t0 = time.monotonic()

    if args.dry_run:
        latencies, success_count, errors = await _run_dry(args.concurrency, args.duration_seconds)
        target_label = None
        mode = "dry-run"
        notes = ["Synthetic in-process run; not a real measurement."] + list(args.notes)
    else:
        if not args.target:
            print("ERROR: --target is required unless --dry-run", file=sys.stderr)
            return 2
        token = args.token or os.environ.get("ACP_TOKEN", "")
        if not (token and args.tenant and args.agent):
            print("ERROR: --token, --tenant, --agent are required for live runs", file=sys.stderr)
            return 2
        latencies, success_count, errors = await _run_live(
            target=args.target,
            token=token,
            tenant_id=args.tenant,
            agent_id=args.agent,
            concurrency=args.concurrency,
            duration_seconds=args.duration_seconds,
            warmup_seconds=args.warmup_seconds,
        )
        target_label = args.target
        mode = "live"
        notes = list(args.notes)

    duration = time.monotonic() - t0

    result = BenchResult(
        harness_version=HARNESS_VERSION,
        environment_label=args.environment_label,
        mode=mode,
        target=target_label,
        started_at_utc=started,
        duration_seconds=round(duration, 3),
        concurrency=args.concurrency,
        warmup_seconds=args.warmup_seconds,
        total_requests=success_count + sum(errors.values()),
        success_count=success_count,
        error_count=sum(errors.values()),
        error_breakdown=errors,
        latency_ms=summarize_latencies(latencies),
        hardware=_hardware_snapshot(),
        notes=notes,
    )

    out_path = args.output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote {out_path}")
    print(
        f"  p50={result.latency_ms['p50']} ms "
        f"p95={result.latency_ms['p95']} ms "
        f"p99={result.latency_ms['p99']} ms "
        f"(n={result.success_count}, errors={result.error_count}, mode={mode})"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
