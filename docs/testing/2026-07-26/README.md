# Aegis testing artifacts — 2026-07-26

Raw output from the test run backing [`../../26-testing.md`](../../26-testing.md).

## Files

**Phase A — chaos engineering (from `/tmp/aegis-v2-run/`):**
- `chaos-runner.sh` — shell driver that runs on the EC2 host, kills a container, samples request behavior, restarts
- `chaos-decision.txt` — kill decision service → 5/5 fail-closed 503, 16 s recovery
- `chaos-abc.txt` — audit + OPA + gateway restart runs
- `chaos-gateway-alb.txt` — rolling gateway restart probed via public ALB
- `chaos-investigate.txt` — OPA env config + audit-chain integrity check + queue status post-chaos

**Phase B — scalability sweep:**
- `scale-sweep.py` — the async load generator (runs inside acp_gateway container to bypass WAF)
- `scale-sweep.txt` — 50→100→250→500→1000→2000 concurrency curve, capturing status codes + latencies

**Phase C — resource metrics:**
- `resources.txt` — docker-stats snapshot of both hosts post-load (CPU + memory per container, host load, disk, network sockets)

## How to reproduce

1. Provision a test tenant + agent + employee key on `aegisagent.in` (or your own deploy).
2. From an EC2 host inside the same VPC (or use ssm-run):
   ```bash
   MODE=kill-decision ./chaos-runner.sh
   MODE=kill-audit    ./chaos-runner.sh
   MODE=kill-opa      ./chaos-runner.sh
   MODE=kill-gateway  ./chaos-runner.sh
   ```
3. To reproduce the scalability sweep:
   ```bash
   docker cp scale-sweep.py acp_gateway:/tmp/scale.py
   docker exec acp_gateway python /tmp/scale.py
   ```
4. Update the tenant + employee-key + agent-id constants at the top of each script for your own environment.

## Interpretation

Every table in `26-testing.md` references either a file in this directory or a specific command. If a number in the report doesn't have provenance you can point at here, flag it — nothing should be uncited.

## Figures (SVG — GitHub renders inline)

Under `figures/`. Rendered with matplotlib from real per-request timing data collected during the test window.

- `fig-1-latency-histograms.svg` — per-class latency histograms (inject, PII, cost, allow)
- `fig-2-latency-cdf.svg` — cumulative distribution overlay of the four classes
- `fig-3-scalability.svg` — sweep 50→2000 workers (latency, RPS, success%)
- `fig-4-chaos-decision.svg` — chaos timeline for kill Decision service
- `fig-5-attack-matrix.svg` — attack coverage bar chart
- `fig-6-cpu-timeseries.svg` — per-container CPU during small-load run

Chart-generation scripts under `scripts/`:
- `mint-fresh.py` — mint a test employee key + agent + permission (bootstrap for the load test)
- `gen-chart-data.py` — collect per-request timings for 5 request classes × 200 samples each
- `render-local.py` — matplotlib SVG generator from the trimmed data

To reproduce the figures from a fresh test run:
```bash
# 1. Provision test creds
python scripts/mint-fresh.py    # prints KEY= and AID=
# 2. Substitute KEY + AID in gen-chart-data.py, then:
docker exec acp_gateway python /tmp/gen-chart-data.py
# 3. Pull /tmp/chart-data.json off the host, then:
python scripts/render-local.py
```
