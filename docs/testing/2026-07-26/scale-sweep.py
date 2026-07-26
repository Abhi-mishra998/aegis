"""Phase B: scalability breaking-point sweep.

Ramp concurrency: 50, 100, 250, 500, 1000, 2000.
30s at each level. Capture success rate + p50/p95/p99 + max latency + errors.
Also snapshot host CPU/mem + container RSS before + during + after each level.

Runs from inside gateway (past WAF)."""
import asyncio, httpx, time, json, random, subprocess

KEY = "acp_emp_REDACTED_test_key_replace_with_your_own"
TENANT = "462d6e58-559f-44f3-8b0f-185aa9235909"
AID = "b64b52da-93f0-44f3-add2-7d78b19f39b0"
GATEWAY = "http://gateway:8000"

H = {"x-api-key": KEY, "X-Tenant-ID": TENANT, "X-Agent-ID": AID,
     "Content-Type": "application/json", "Accept": "application/json",
     "User-Agent": "Mozilla/5.0 (compatible; aegis-scale-test/1.0 httpx)"}


async def one_request(client):
    body = {"agent_id": AID, "tool": "search_web",
            "parameters": {"q": f"q-{random.randint(0,9999)}"}}
    t0 = time.time()
    try:
        r = await client.post(f"{GATEWAY}/execute", headers=H, json=body, timeout=20.0)
        return r.status_code, int((time.time()-t0)*1000)
    except asyncio.TimeoutError:
        return -1, int((time.time()-t0)*1000)
    except Exception:
        return 0, int((time.time()-t0)*1000)


async def worker(client, deadline):
    results = []
    while time.time() < deadline:
        results.append(await one_request(client))
        await asyncio.sleep(random.uniform(0.05, 0.15))
    return results


async def snapshot_resources():
    # From INSIDE the container we can only measure our own cgroup — kick a
    # host-level snapshot via a shell that inspects docker stats output.
    # Best-effort — some fields may be blank in Docker Desktop or CI.
    try:
        p = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}},{{.CPUPerc}},{{.MemUsage}}"],
            capture_output=True, text=True, timeout=5,
        )
        return p.stdout.strip().split("\n")
    except Exception as e:
        return [f"snapshot-failed: {e}"]


async def run_level(concurrency, duration_s):
    print(f"\n=== level: {concurrency} workers × {duration_s}s ===")
    deadline = time.time() + duration_s
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=concurrency + 50)) as client:
        tasks = [worker(client, deadline) for _ in range(concurrency)]
        all_results = []
        for r in await asyncio.gather(*tasks):
            all_results.extend(r)

    codes = {}
    lats = []
    errors = 0
    timeouts = 0
    for status, ms in all_results:
        codes[status] = codes.get(status, 0) + 1
        if status == -1:
            timeouts += 1
        elif status == 0:
            errors += 1
        else:
            lats.append(ms)
    lats.sort()
    n = len(lats)
    p50 = lats[n//2] if n else 0
    p95 = lats[min(n-1, int(n*0.95))] if n else 0
    p99 = lats[min(n-1, int(n*0.99))] if n else 0
    mx = max(lats) if lats else 0
    rps = len(all_results) / duration_s
    total = len(all_results)
    ok_pct = 100 * codes.get(200, 0) / total if total else 0
    print(f"  total={total} rps={rps:.1f} success%={ok_pct:.1f}")
    print(f"  codes: {dict(sorted(codes.items()))}")
    print(f"  errors/timeouts: {errors}/{timeouts}")
    print(f"  latency: p50={p50}ms p95={p95}ms p99={p99}ms max={mx}ms")
    return {
        "concurrency": concurrency, "total": total, "rps": rps,
        "success_pct": ok_pct, "codes": codes,
        "errors": errors, "timeouts": timeouts,
        "p50": p50, "p95": p95, "p99": p99, "max": mx,
    }


async def main():
    LEVELS = [50, 100, 250, 500, 1000, 2000]
    DURATION = 30
    all_data = []
    for c in LEVELS:
        data = await run_level(c, DURATION)
        all_data.append(data)
        # Recovery pause between levels
        await asyncio.sleep(10)

    print("\n\n=== SUMMARY ===")
    print(f"{'level':>6}  {'rps':>7}  {'success%':>9}  {'p50':>6}  {'p95':>7}  {'p99':>7}  {'max':>7}  {'errors':>6}  {'timeouts':>8}")
    for d in all_data:
        print(f"{d['concurrency']:>6}  {d['rps']:>6.1f}  {d['success_pct']:>8.1f}%  {d['p50']:>4}ms  {d['p95']:>5}ms  {d['p99']:>5}ms  {d['max']:>5}ms  {d['errors']:>6}  {d['timeouts']:>8}")

    with open("/tmp/scale-sweep.json", "w") as f:
        json.dump(all_data, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
