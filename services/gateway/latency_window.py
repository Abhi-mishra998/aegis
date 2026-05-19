"""Sliding-window latency tracker for /status + /system/health.

Before this module the two endpoints reported wildly different p95
numbers because they computed from different sources:

* `/system/health` (line 1678 main.py before this sprint) sorted the
  12 downstream `/health` probe latencies on each call and picked
  element ``int(12 * 0.95) - 1 == 10``. That's not "p95 of anything"
  in any useful sense — it's the second-slowest probe round-trip.
* `/status` re-read the same number out of /system/health's response.
  Customers polling both endpoints simultaneously saw 11ms vs 34ms
  because each call materialised a different probe sample set.

This module fixes the shape, not just the number. Two singletons:

* ``gateway_internal_window`` — request-received → response-sent
  durations recorded by `SecurityMiddleware._dispatch_with_resilience`.
  Answers "how fast is the gateway itself?".
* ``end_to_end_window`` — probe RTT recorded by `system_health`'s
  per-service `/health` probes. Answers "what does a client see
  including downstream round-trips?".

Both produce the canonical shape

    {
        "scope":           "gateway_internal" | "end_to_end",
        "window_seconds":  <int>,
        "p50_ms":          <int>,
        "p95_ms":          <int>,
        "p99_ms":          <int>,
        "request_count":   <int>,
        "computed_at":     <ISO-8601 UTC>,
    }

so callers don't have to guess. The Grafana dashboards consume both
side-by-side; ops_endpoints docs explain when to read which.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any


_DEFAULT_WINDOW_SECONDS = 60
_DEFAULT_MAX_SAMPLES    = 100_000  # ~28h at 1 req/s, ~10 min at 100 req/s


class LatencyWindow:
    """Thread-safe rolling window of (timestamp_monotonic, latency_ms).

    `record(latency_ms)` is the hot-path entry. `summary(scope)` is the
    cold-path read called by /status + /system/health. Stale samples
    (older than window_seconds) are evicted lazily on read so the
    record path stays O(1).
    """

    def __init__(
        self,
        *,
        scope: str,
        window_seconds: int = _DEFAULT_WINDOW_SECONDS,
        max_samples: int = _DEFAULT_MAX_SAMPLES,
    ) -> None:
        if scope not in ("gateway_internal", "end_to_end"):
            raise ValueError(f"invalid scope {scope!r}")
        self.scope = scope
        self.window_seconds = int(window_seconds)
        self._samples: "deque[tuple[float, float]]" = deque(maxlen=int(max_samples))
        self._lock = threading.Lock()

    # ── Producer ──────────────────────────────────────────────────────
    def record(self, latency_ms: float) -> None:
        if latency_ms < 0:
            return
        now = time.monotonic()
        with self._lock:
            self._samples.append((now, float(latency_ms)))

    # ── Consumer ──────────────────────────────────────────────────────
    def _live_samples(self) -> list[float]:
        """Return latency_ms values within the window; evict older entries."""
        cutoff = time.monotonic() - self.window_seconds
        with self._lock:
            # Lazy eviction from the left.
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            return [v for _, v in self._samples]

    def percentile(self, p: float) -> float:
        """Linear-interp percentile in ms; 0.0 on empty window."""
        if not (0.0 <= p <= 1.0):
            raise ValueError("p must be in [0, 1]")
        vals = sorted(self._live_samples())
        if not vals:
            return 0.0
        if len(vals) == 1:
            return vals[0]
        # nearest-rank with safe clamp — close enough for SRE-grade
        # signals at this volume; no need for HdrHistogram.
        k = max(0, min(len(vals) - 1, int(round(p * (len(vals) - 1)))))
        return vals[k]

    def count(self) -> int:
        return len(self._live_samples())

    def summary(self) -> dict[str, Any]:
        """Canonical-shape dict consumed by /status + /system/health."""
        # Single eviction pass for the whole summary so all four metrics
        # agree on the same sample set.
        vals = sorted(self._live_samples())
        if not vals:
            p50 = p95 = p99 = 0.0
        elif len(vals) == 1:
            p50 = p95 = p99 = vals[0]
        else:
            n = len(vals)
            p50 = vals[max(0, min(n - 1, int(round(0.50 * (n - 1)))))]
            p95 = vals[max(0, min(n - 1, int(round(0.95 * (n - 1)))))]
            p99 = vals[max(0, min(n - 1, int(round(0.99 * (n - 1)))))]
        return {
            "scope":          self.scope,
            "window_seconds": self.window_seconds,
            "p50_ms":         int(round(p50)),
            "p95_ms":         int(round(p95)),
            "p99_ms":         int(round(p99)),
            "request_count":  len(vals),
            "computed_at":    datetime.now(UTC).isoformat(),
        }

    # ── Test hook ─────────────────────────────────────────────────────
    def reset_for_tests(self) -> None:
        with self._lock:
            self._samples.clear()


# Module-level singletons. Importers must not instantiate new ones for
# /status or /system/health — that would re-fragment the two scopes.
gateway_internal_window = LatencyWindow(scope="gateway_internal")
end_to_end_window       = LatencyWindow(scope="end_to_end")
