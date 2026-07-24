"""ATF v3.2 §12.1 — Reference Workload harness.

Locust-based load generator that hits the Aegis Gate with the exact
mix specified in §12.1 so measurements are comparable across runs +
across deployments. Every measured number references this workload id
so cherry-picked numbers can't sneak into a sales deck.

Workload id: ``atf_ref_v3_2_2026_07_22``

Invocation (from repo root):

    ACP_TOKEN=<test-jwt> \\
    ACP_TARGET=https://gate.dev.aegisagent.in \\
    .venv/bin/locust -f tests/load/atf_reference_workload.py \\
        --host $ACP_TARGET \\
        --users 20 --spawn-rate 5 \\
        --run-time 30m \\
        --headless \\
        --csv reports/bench/atf_ref_$(date +%Y%m%d_%H%M)

Publish the measured p50/p99 latencies + throughput back into §12.2
(replacing `*TBD*`) — one row per (workload_id, deployment_id, date).

Workload spec (§12.1):
    Scale       1 tenant, 50 agents, 20 concurrent steady, burst 50
    Volume      1M gated actions/day  → 11.6 rps avg, 60 rps p99
    Class mix   85% C0, 10% C1, 4.5% C2, 0.5% C3
    Payload     req p50 2KB / p99 32KB, resp p50 8KB / p99 128KB
    Delegation  depth 1 on 5% of calls
    Escalations 0.2% of actions
"""
from __future__ import annotations

import os
import random
import string
import time

from locust import HttpUser, between, events, task

# ─────────────────────────────────────────────────────────────
# Workload identity — every measurement cites this string so a run
# with different mixes cannot mistakenly be reported under this id.
# ─────────────────────────────────────────────────────────────
WORKLOAD_ID = "atf_ref_v3_2_2026_07_22"

# §12.1 class mix — precomputed cumulative probabilities.
_CLASS_MIX = (
    ("C0", 0.85),
    ("C1", 0.10),
    ("C2", 0.045),
    ("C3", 0.005),
)
_CUMULATIVE = []
_running = 0.0
for _cls, _p in _CLASS_MIX:
    _running += _p
    _CUMULATIVE.append((_cls, _running))

# 50 agents, one tenant. Real deployments randomize per-tenant agent
# pool per test; the benchmark keeps the tenant fixed for comparability.
_TENANT_ID = os.getenv("ACP_BENCH_TENANT_ID", "00000000-0000-0000-0000-000000000042")
_AGENTS = [f"bench-agent-{i:02d}" for i in range(50)]

# Escalation + delegation probabilities from §12.1.
_P_DELEGATION = 0.05
_P_ESCALATION = 0.002


def _pick_class() -> str:
    r = random.random()
    for cls, cum in _CUMULATIVE:
        if r <= cum:
            return cls
    return "C0"


def _payload_size_bytes() -> int:
    """§12.1: request p50 2 KB / p99 32 KB. Rough approximation via
    log-normal draw — good enough for a comparability benchmark; a real
    sales-quality run replaces this with a captured production trace."""
    return int(random.lognormvariate(mu=7.6, sigma=1.0))  # ~2KB median


def _pad_body(base: dict, target_bytes: int) -> dict:
    """Grow the request body to approximately `target_bytes` via a
    string field. Avoids per-request allocator jitter dominating the
    latency measurement — Gate overhead is what we're measuring."""
    existing_size = len(str(base))
    pad_len = max(0, target_bytes - existing_size)
    base["_bench_pad"] = "".join(random.choices(string.ascii_lowercase, k=pad_len))
    return base


class ATFReferenceUser(HttpUser):
    """One simulated agent making C0/C1/C2/C3 gated actions per §12.1 mix.

    Wait time targets ~11.6 rps aggregate at 20 concurrent users =
    ~1.7s between requests per user; between(0.5, 3.0) samples that
    interval loosely enough to see p99 bursts.
    """

    wait_time = between(0.5, 3.0)

    def on_start(self) -> None:
        token = os.getenv("ACP_TOKEN", "")
        if token:
            self.client.headers["Authorization"] = f"Bearer {token}"
        self.client.headers["X-Tenant-ID"] = _TENANT_ID
        # Fix this user to a single agent for the run — mirrors a real
        # long-lived agent process rather than a synthetic churn.
        self._agent = random.choice(_AGENTS)
        self.client.headers["X-Agent-ID"] = self._agent

    @task(85)
    def c0_read(self) -> None:
        self._issue(_pick_class_forced="C0", tool="get_record",
                    payload={"id": random.randint(1, 100_000)})

    @task(10)
    def c1_write_reversible(self) -> None:
        self._issue(_pick_class_forced="C1", tool="update_record",
                    payload={"id": random.randint(1, 100_000),
                             "field": "note",
                             "value": "bench"})

    @task(5)
    def c2_write_hard(self) -> None:
        # Split: 4.5% C2 + 0.5% C3 per §12.1. Random split within this task
        # keeps the weight rebalance simple.
        klass = "C3" if random.random() < 0.10 else "C2"
        tool = "send_payment" if klass == "C3" else "delete_record"
        self._issue(_pick_class_forced=klass, tool=tool,
                    payload={"id": random.randint(1, 100_000),
                             "amount_usd": random.randint(100, 12_000)})

    def _issue(self, *, _pick_class_forced: str, tool: str, payload: dict) -> None:
        headers: dict[str, str] = {}
        # 5% of calls carry a delegation chain header — mirror §12.1.
        if random.random() < _P_DELEGATION:
            headers["X-Delegation-Depth"] = "1"
        # Escalation probability is per-request; the server-side policy
        # will emit ESCALATE on 0.2% of matching-shape calls.
        if random.random() < _P_ESCALATION:
            payload["_bench_force_escalation"] = True

        target_bytes = _payload_size_bytes()
        body = _pad_body({
            "tool":   tool,
            "params": payload,
            "workload_id":  WORKLOAD_ID,
            "action_class": _pick_class_forced,
        }, target_bytes)

        with self.client.post(
            "/execute",
            json=body, headers=headers, catch_response=True,
            name=f"/execute [{_pick_class_forced}]",
        ) as r:
            # 200 = allow, 403 = deny/escalate — both are legitimate
            # measured outcomes for benchmarking Gate overhead. 5xx is
            # not a benchmark signal, it's a failure.
            if r.status_code >= 500:
                r.failure(f"gate 5xx: {r.status_code}")


@events.test_start.add_listener
def _log_start(environment, **_kw):  # type: ignore[no-untyped-def]
    print(f"[atf-ref] workload_id={WORKLOAD_ID} "
          f"tenant={_TENANT_ID[:8]} start_ts={time.time():.0f}")


@events.test_stop.add_listener
def _log_stop(environment, **_kw):  # type: ignore[no-untyped-def]
    stats = environment.stats
    total = stats.total
    print(
        f"[atf-ref] workload_id={WORKLOAD_ID} "
        f"stop_ts={time.time():.0f} "
        f"total_requests={total.num_requests} "
        f"failures={total.num_failures} "
        f"rps={total.total_rps:.2f} "
        f"p50={total.get_response_time_percentile(0.5):.0f}ms "
        f"p99={total.get_response_time_percentile(0.99):.0f}ms"
    )
