"""
Kubernetes-specific risk signal detectors.

Each detector takes a sliding window of recent operations and returns a
(risk_score: float, triggered: bool, reason: str) tuple.

Risk scores are bounded [0.0, 1.0] and monotonic with respect to the
severity of the detected pattern.  The signals are combined by the
kubectl_wrapper before passing them as ACP execute metadata.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass
class K8sOp:
    """A single kubectl operation observation."""
    ts: float
    verb: str             # get, list, delete, scale, create, patch, exec
    resource: str         # pod, deployment, namespace, clusterrole, secret, node
    namespace: str | None = None
    name: str | None = None
    risk: float = 0.0


@dataclass
class SignalResult:
    name: str
    score: float
    triggered: bool
    reason: str


# Window size in seconds for sliding-window detectors
_WINDOW_S = 60.0

_DESTRUCTIVE_VERBS = frozenset({"delete", "remove"})
_ESCALATION_RESOURCES = frozenset({"clusterrole", "clusterrolebinding", "rolebinding"})
_SECRETS_RESOURCES = frozenset({"secret", "secrets"})
_SENSITIVE_NS = frozenset({"production", "kube-system"})


class K8sSignalEngine:
    """
    Sliding-window behavioral detector for Kubernetes operations.

    Instantiate once per demo/agent session and call ``observe()`` for every
    kubectl operation (both allowed and denied — the pattern matters).
    Then call ``evaluate()`` to get per-signal scores for the ACP execute.
    """

    def __init__(self, window_s: float = _WINDOW_S) -> None:
        self._window_s = window_s
        self._ops: deque[K8sOp] = deque()

    def observe(self, op: K8sOp) -> None:
        self._ops.append(op)
        self._evict()

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._window_s
        while self._ops and self._ops[0].ts < cutoff:
            self._ops.popleft()

    def _recent(self) -> list[K8sOp]:
        self._evict()
        return list(self._ops)

    # ── Individual signal detectors ───────────────────────────────────────────

    def destructive_deletion_loop(self) -> SignalResult:
        """Repeated delete ops in the window — escalates with count."""
        ops = self._recent()
        deletes = [o for o in ops if o.verb in _DESTRUCTIVE_VERBS]
        count = len(deletes)
        if count == 0:
            return SignalResult("destructive_deletion_loop", 0.0, False, "no deletes in window")
        score = min(1.0, count * 0.18)
        triggered = count >= 3
        reason = f"{count} delete ops in {self._window_s}s window"
        return SignalResult("destructive_deletion_loop", round(score, 3), triggered, reason)

    def namespace_sweep(self) -> SignalResult:
        """Sequential enumeration of multiple namespaces."""
        ops = self._recent()
        ns_seen = {o.namespace for o in ops if o.namespace and o.verb in ("get", "list", "describe")}
        count = len(ns_seen)
        if count <= 1:
            return SignalResult("namespace_sweep", 0.0, False, "single namespace activity")
        score = min(1.0, (count - 1) * 0.20)
        triggered = count >= 4
        reason = f"enumerated {count} namespaces: {', '.join(sorted(ns_seen))}"
        return SignalResult("namespace_sweep", round(score, 3), triggered, reason)

    def pod_deletion_storm(self) -> SignalResult:
        """Multiple pod deletes in a short burst."""
        ops = self._recent()
        pod_deletes = [o for o in ops
                       if o.verb in _DESTRUCTIVE_VERBS and o.resource in ("pod", "pods")]
        count = len(pod_deletes)
        if count == 0:
            return SignalResult("pod_deletion_storm", 0.0, False, "no pod deletes")
        score = min(1.0, count * 0.22)
        triggered = count >= 3
        return SignalResult("pod_deletion_storm", round(score, 3), triggered,
                            f"{count} pod deletes in window")

    def privilege_escalation_recon(self) -> SignalResult:
        """Suspicious RBAC / service-account enumeration."""
        ops = self._recent()
        rbac_reads = [o for o in ops
                      if o.resource in _ESCALATION_RESOURCES
                      and o.verb in ("get", "list", "describe")]
        count = len(rbac_reads)
        if count == 0:
            return SignalResult("privilege_escalation_recon", 0.0, False, "no RBAC enumeration")
        score = min(1.0, count * 0.25)
        triggered = count >= 2
        return SignalResult("privilege_escalation_recon", round(score, 3), triggered,
                            f"RBAC enumeration: {count} reads in window")

    def secrets_recon(self) -> SignalResult:
        """Multiple secrets reads — credential harvesting pattern."""
        ops = self._recent()
        secret_reads = [o for o in ops
                        if o.resource in _SECRETS_RESOURCES
                        and o.verb in ("get", "list", "describe")]
        count = len(secret_reads)
        if count == 0:
            return SignalResult("secrets_recon", 0.0, False, "no secrets reads")
        score = min(1.0, count * 0.30)
        triggered = count >= 2
        return SignalResult("secrets_recon", round(score, 3), triggered,
                            f"secrets access: {count} reads in window")

    def automation_runaway(self) -> SignalResult:
        """High op frequency — runaway loop detection."""
        ops = self._recent()
        count = len(ops)
        if count <= 5:
            return SignalResult("automation_runaway", 0.0, False, f"{count} ops in window")
        score = min(1.0, (count - 5) * 0.07)
        triggered = count >= 15
        return SignalResult("automation_runaway", round(score, 3), triggered,
                            f"{count} ops in {self._window_s}s window (runaway threshold=15)")

    def cross_namespace_destructive_escalation(self) -> SignalResult:
        """Destructive ops spreading across multiple namespaces."""
        ops = self._recent()
        dest_ns = {o.namespace for o in ops
                   if o.verb in _DESTRUCTIVE_VERBS and o.namespace}
        count = len(dest_ns)
        if count <= 1:
            return SignalResult("cross_namespace_escalation", 0.0, False,
                                "destructive ops contained to single namespace")
        score = min(1.0, count * 0.30)
        triggered = count >= 2
        return SignalResult("cross_namespace_escalation", round(score, 3), triggered,
                            f"destructive ops in {count} namespaces: {', '.join(sorted(dest_ns))}")

    # ── Aggregate ─────────────────────────────────────────────────────────────

    def evaluate(self) -> dict:
        """Return all signal scores as a dict for ACP execute metadata."""
        signals = [
            self.destructive_deletion_loop(),
            self.namespace_sweep(),
            self.pod_deletion_storm(),
            self.privilege_escalation_recon(),
            self.secrets_recon(),
            self.automation_runaway(),
            self.cross_namespace_destructive_escalation(),
        ]
        composite = min(1.0, max(s.score for s in signals))
        triggered = [s.name for s in signals if s.triggered]
        return {
            "k8s_composite_risk": round(composite, 3),
            "k8s_triggered_signals": triggered,
            "k8s_signals": {s.name: {"score": s.score, "triggered": s.triggered,
                                      "reason": s.reason}
                            for s in signals},
        }

    def aggregate_risk(self) -> float:
        """Single float risk [0,1] for the current window."""
        results = self.evaluate()
        return float(results["k8s_composite_risk"])
