"""10 000-VU burst LoadTestShape for the v2.0 Track D2 stress test.

Pairs with tests/load/v2_realistic_user.py — same per-user behaviour, same
realistic 60/15/10/10/5 mix; this file only defines the *shape* of the
load over time so a customer can see Aegis under a 10× burst above the
sustained D1 rate.

Shape per SPRINT.md §7 D2:

   users
    10000 -----------+
                    /|
                   / |   5 minute hold
                  /  |
             100 +   +------+
                 |          |\\
                 |          | \\
                 +----------+--+---->  time
              0  60s        +5m  +60s
              ramp-up      hold   ramp-down

Pass criteria (asserted post-run by tests/load/post_run_checks.py +
hand-rendered into reports/load-test-2026-Q3/10k-burst-report.md):

  p95 latency during the burst window      < 1500 ms
  no 5xx storm (shed-load engages cleanly)
  behavior firewall stays available
    (zero `behavior_service_unavailable` audit rows during the run)
  p95 returns to D1 baseline within 90 s after ramp-down ends

Run via locust CLI (the orchestrator does this — direct invocation works):

    locust -f tests/load/v2_realistic_user.py:V2RealisticUser \\
           -f tests/load/v2_realistic_burst.py:BurstShape \\
           --headless --host https://ha.aegisagent.in \\
           --csv reports/load-test-2026-Q3/10k-burst/locust

The orchestrator passes `--user-class V2RealisticUser` and
`--shape burst_10k` so the same user file serves both D1 and D2.
"""
from __future__ import annotations

from locust import LoadTestShape

# --------------------------------------------------------------------------- #
# Shape phases — durations in seconds, target user count, ramp-rate /s        #
# --------------------------------------------------------------------------- #

_PHASES = (
    # (duration_s_from_start, target_users, spawn_rate_per_second)
    (0,    100,   50),    # warm-up baseline — 100 VUs for the first second
    (60,   10000, 165),   # ramp to 10 000 over 60s (~165 vu/s)
    (60 + 300, 10000, 1), # hold 10 000 for 5 minutes
    (60 + 300 + 60, 100, 200),  # ramp down to 100 over 60s
    (60 + 300 + 60 + 60, 0, 200),  # ramp down to 0 over 60s (cooldown / recovery window)
)
_TOTAL_DURATION = _PHASES[-1][0]


class BurstShape(LoadTestShape):
    """SPRINT v2.0 §7 D2 burst-load shape.

    Locust calls `tick()` roughly once per second. Returning None ends
    the test. Returning (users, spawn_rate) sets the runner's target
    for the next second.
    """

    def tick(self):
        run_time = self.get_run_time()
        if run_time > _TOTAL_DURATION:
            return None
        for stop_at, users, rate in _PHASES:
            if run_time < stop_at:
                return (users, rate)
        return None
