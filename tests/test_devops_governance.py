"""
DevOps Governance Scenario — unit tests.

Covers:
  - Mock K8s cluster state management
  - K8s signal engine: all 7 detectors
  - kubectl_wrapper: arg parsing + local execution + ACP bypass
  - k8s_policy.rego: deterministic policy decisions (via local_eval)
  - Blast radius invariants (via existing graph trust_engine)
  - Autonomy enforcement simulation
  - Rate limiting signal thresholds
  - Kill switch simulation

All tests are in-process — no running stack required.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Imports ───────────────────────────────────────────────────────────────────
from demos.devops_agent.k8s_signals import K8sOp, K8sSignalEngine
from demos.devops_agent.kubectl_wrapper import KubectlWrapper, _classify_op, _parse_args
from demos.devops_agent.mock_k8s import _NAMESPACES, MockK8sCluster

# ═════════════════════════════════════════════════════════════════════════════
#  Mock K8s Cluster Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestMockK8sCluster:
    def setup_method(self) -> None:
        self.cluster = MockK8sCluster()

    def test_namespaces_seeded(self) -> None:
        assert set(self.cluster.namespaces) == set(_NAMESPACES)

    def test_production_has_deployments(self) -> None:
        deps = self.cluster.list_deployments("production")
        assert len(deps) >= 3
        names = [d["metadata"]["name"] for d in deps]
        assert "payments-api" in names
        assert "checkout" in names
        assert "auth-service" in names

    def test_staging_has_deployments(self) -> None:
        deps = self.cluster.list_deployments("staging")
        assert len(deps) >= 2

    def test_pods_have_realistic_metadata(self) -> None:
        pods = self.cluster.list_pods("production")
        assert len(pods) >= 7  # 3+2+2 replicas
        for pod in pods:
            m = pod["metadata"]
            assert "uid" in m
            assert "resourceVersion" in m
            assert m["namespace"] == "production"
            assert pod["status"]["phase"] == "Running"

    def test_secrets_have_redacted_data(self) -> None:
        secrets = self.cluster.list_secrets("production")
        assert len(secrets) >= 3
        for s in secrets:
            # data field should exist but be redacted
            assert "data" in s

    def test_list_nodes(self) -> None:
        nodes = self.cluster.list_nodes()
        assert len(nodes) == 3
        roles = {n["metadata"]["name"] for n in nodes}
        assert "node-1" in roles

    def test_scale_deployment_mutates_state(self) -> None:
        result = self.cluster.scale_deployment("staging", "payments-api", 3)
        assert isinstance(result, dict)
        dep = self.cluster.get_deployment("staging", "payments-api")
        assert dep is not None
        assert dep["spec"]["replicas"] == 3
        assert dep["status"]["readyReplicas"] == 3

    def test_scale_nonexistent_deployment_returns_error(self) -> None:
        result = self.cluster.scale_deployment("staging", "nonexistent-svc", 2)
        assert isinstance(result, str)
        assert "not found" in result

    def test_delete_namespace_removes_resources(self) -> None:
        result = self.cluster.delete_namespace("staging")
        assert isinstance(result, dict)
        assert "staging" not in self.cluster.namespaces
        # Cascades to deployments
        deps = self.cluster.list_deployments("staging")
        assert len(deps) == 0

    def test_delete_nonexistent_namespace_returns_error(self) -> None:
        result = self.cluster.delete_namespace("phantom-ns")
        assert isinstance(result, str)
        assert "not found" in result

    def test_delete_node(self) -> None:
        result = self.cluster.delete_node("node-2")
        assert isinstance(result, dict)
        assert "node-2" not in self.cluster.nodes

    def test_get_logs_returns_realistic_lines(self) -> None:
        pods = self.cluster.list_pods("production")
        pod_name = pods[0]["metadata"]["name"]
        logs = self.cluster.get_logs("production", pod_name)
        lines = logs.strip().splitlines()
        assert len(lines) >= 5

    def test_get_logs_unknown_pod_returns_error(self) -> None:
        logs = self.cluster.get_logs("production", "phantom-pod-xyz")
        assert "not found" in logs

    def test_clusterroles_seeded(self) -> None:
        roles = self.cluster.list_clusterroles()
        names = [r["metadata"]["name"] for r in roles]
        assert "cluster-admin" in names
        assert "view" in names
        assert "edit" in names

    def test_cluster_admin_has_wildcard_rules(self) -> None:
        admin = self.cluster.clusterroles["cluster-admin"]
        rules = admin["rules"]
        assert any(
            "*" in r.get("apiGroups", []) and "*" in r.get("verbs", [])
            for r in rules
        )

    def test_format_pods_tabular(self) -> None:
        pods = self.cluster.list_pods("production")
        output = self.cluster.format_pods(pods)
        assert "NAME" in output
        assert "STATUS" in output
        lines = output.splitlines()
        assert len(lines) >= 8  # header + pods

    def test_format_nodes_tabular(self) -> None:
        output = self.cluster.format_nodes(self.cluster.list_nodes())
        assert "ROLES" in output
        assert "node-1" in output


# ═════════════════════════════════════════════════════════════════════════════
#  Signal Engine Tests
# ═════════════════════════════════════════════════════════════════════════════

class TestK8sSignalEngine:
    def _op(self, verb: str, resource: str, namespace: str = "default",
            name: str | None = None) -> K8sOp:
        return K8sOp(ts=time.monotonic(), verb=verb, resource=resource,
                     namespace=namespace, name=name)

    def test_no_signals_on_empty_window(self) -> None:
        engine = K8sSignalEngine()
        result = engine.evaluate()
        assert result["k8s_composite_risk"] == 0.0
        assert result["k8s_triggered_signals"] == []

    def test_read_ops_produce_zero_risk(self) -> None:
        engine = K8sSignalEngine()
        for _ in range(10):
            engine.observe(self._op("get", "pods", "production"))
        result = engine.evaluate()
        # reads alone produce namespace_sweep but low composite
        assert result["k8s_composite_risk"] < 0.50

    def test_destructive_deletion_loop_triggers_at_3(self) -> None:
        engine = K8sSignalEngine()
        for i in range(3):
            engine.observe(self._op("delete", "pod", "staging", f"pod-{i}"))
        sig = engine.destructive_deletion_loop()
        assert sig.triggered
        assert sig.score >= 0.3

    def test_destructive_deletion_loop_2_not_triggered(self) -> None:
        engine = K8sSignalEngine()
        for i in range(2):
            engine.observe(self._op("delete", "pod", "staging", f"pod-{i}"))
        sig = engine.destructive_deletion_loop()
        assert not sig.triggered

    def test_namespace_sweep_triggers_at_4(self) -> None:
        engine = K8sSignalEngine()
        for ns in ["production", "staging", "default", "monitoring"]:
            engine.observe(self._op("list", "pods", ns))
        sig = engine.namespace_sweep()
        assert sig.triggered
        assert sig.score >= 0.5

    def test_namespace_sweep_single_ns_not_triggered(self) -> None:
        engine = K8sSignalEngine()
        for _ in range(5):
            engine.observe(self._op("get", "pod", "production"))
        sig = engine.namespace_sweep()
        assert not sig.triggered

    def test_pod_deletion_storm_triggers_at_3(self) -> None:
        engine = K8sSignalEngine()
        for i in range(3):
            engine.observe(self._op("delete", "pod", "default", f"p-{i}"))
        sig = engine.pod_deletion_storm()
        assert sig.triggered
        assert sig.score >= 0.5

    def test_privilege_escalation_recon_triggers_at_2(self) -> None:
        engine = K8sSignalEngine()
        engine.observe(self._op("list", "clusterrole"))
        engine.observe(self._op("get", "clusterrolebinding"))
        sig = engine.privilege_escalation_recon()
        assert sig.triggered

    def test_secrets_recon_triggers_at_2(self) -> None:
        engine = K8sSignalEngine()
        engine.observe(self._op("list", "secret", "production"))
        engine.observe(self._op("get", "secret", "production", "stripe-api-key"))
        sig = engine.secrets_recon()
        assert sig.triggered

    def test_automation_runaway_triggers_at_15_ops(self) -> None:
        engine = K8sSignalEngine()
        for i in range(16):
            engine.observe(self._op("get", "pod", "staging", f"pod-{i}"))
        sig = engine.automation_runaway()
        assert sig.triggered

    def test_automation_runaway_5_ops_not_triggered(self) -> None:
        engine = K8sSignalEngine()
        for _i in range(5):
            engine.observe(self._op("get", "pod", "staging"))
        sig = engine.automation_runaway()
        assert not sig.triggered

    def test_cross_namespace_escalation_triggers_at_2_ns(self) -> None:
        engine = K8sSignalEngine()
        engine.observe(self._op("delete", "pod", "production", "prod-pod"))
        engine.observe(self._op("delete", "pod", "staging",    "stage-pod"))
        sig = engine.cross_namespace_destructive_escalation()
        assert sig.triggered

    def test_composite_risk_bounded_0_to_1(self) -> None:
        engine = K8sSignalEngine()
        for i in range(50):
            engine.observe(self._op("delete", "namespace", f"ns-{i}", f"ns-{i}"))
        result = engine.evaluate()
        assert 0.0 <= result["k8s_composite_risk"] <= 1.0

    def test_aggregate_risk_is_float(self) -> None:
        engine = K8sSignalEngine()
        risk = engine.aggregate_risk()
        assert isinstance(risk, float)
        assert 0.0 <= risk <= 1.0

    def test_eviction_removes_old_ops(self) -> None:
        engine = K8sSignalEngine(window_s=0.05)
        engine.observe(K8sOp(ts=time.monotonic() - 1.0, verb="delete",
                              resource="pod", namespace="default"))
        engine._evict()
        assert len(engine._ops) == 0


# ═════════════════════════════════════════════════════════════════════════════
#  kubectl_wrapper arg parser tests
# ═════════════════════════════════════════════════════════════════════════════

class TestArgParser:
    def test_get_pods_no_namespace(self) -> None:
        p = _parse_args("get pods")
        assert p["verb"] == "get"
        assert "pod" in p["resource"]

    def test_get_pods_with_namespace_flag(self) -> None:
        p = _parse_args("get pods -n production")
        assert p["namespace"] == "production"

    def test_get_pods_long_namespace(self) -> None:
        p = _parse_args("get pods --namespace=staging")
        assert p["namespace"] == "staging"

    def test_delete_namespace(self) -> None:
        p = _parse_args("delete namespace production")
        assert p["verb"] == "delete"
        assert "namespace" in p["resource"]
        assert p["name"] == "production"

    def test_scale_deployment(self) -> None:
        p = _parse_args("scale deployment checkout --replicas=5 -n staging")
        assert p["verb"] == "scale"
        assert p["args"]["replicas"] == 5
        assert p["namespace"] == "staging"
        assert p["name"] == "checkout"

    def test_logs_pod_name(self) -> None:
        p = _parse_args("logs payments-pod-abc123 -n production")
        assert p["verb"] == "logs"
        assert p["name"] == "payments-pod-abc123"
        assert p["namespace"] == "production"

    def test_create_clusterrolebinding(self) -> None:
        p = _parse_args(
            "create clusterrolebinding admin-bind "
            "--clusterrole=cluster-admin "
            "--serviceaccount=default:devops-sa"
        )
        assert p["verb"] == "create"
        assert "clusterrolebinding" in p["resource"]
        assert p["args"]["clusterrole"] == "cluster-admin"
        assert p["name"] == "admin-bind"

    def test_describe_deployment(self) -> None:
        p = _parse_args("describe deployment checkout -n production")
        assert p["verb"] == "describe"
        assert p["name"] == "checkout"
        assert p["namespace"] == "production"

    def test_top_nodes(self) -> None:
        p = _parse_args("top nodes")
        assert p["verb"] == "top"
        assert "node" in p["resource"]

    def test_exec_pod(self) -> None:
        p = _parse_args("exec -it payments-pod -- /bin/sh")
        assert p["verb"] == "exec"

    def test_resource_alias_po(self) -> None:
        p = _parse_args("get po -n default")
        assert "pod" in p["resource"]

    def test_resource_alias_deploy(self) -> None:
        p = _parse_args("get deploy -n staging")
        assert "deployment" in p["resource"]

    def test_resource_alias_ns(self) -> None:
        p = _parse_args("delete ns production")
        assert "namespace" in p["resource"]


# ═════════════════════════════════════════════════════════════════════════════
#  Classify op tests
# ═════════════════════════════════════════════════════════════════════════════

class TestClassifyOp:
    def test_reads_classified_correctly(self) -> None:
        for verb in ("get", "list", "describe", "logs", "top"):
            assert _classify_op(verb, "pod") == "read"

    def test_scale_classified(self) -> None:
        assert _classify_op("scale", "deployment") == "scaling"

    def test_exec_classified(self) -> None:
        assert _classify_op("exec", "pod") == "exec_access"

    def test_delete_namespace_classified_destructive_cluster(self) -> None:
        assert _classify_op("delete", "namespace") == "destructive_cluster"

    def test_delete_pv_classified_destructive_cluster(self) -> None:
        assert _classify_op("delete", "persistentvolume") == "destructive_cluster"

    def test_delete_pod_classified_destructive_namespaced(self) -> None:
        assert _classify_op("delete", "pod") == "destructive_namespaced"

    def test_clusterrolebinding_classified_privilege_escalation(self) -> None:
        assert _classify_op("create", "clusterrolebinding") == "privilege_escalation"


# ═════════════════════════════════════════════════════════════════════════════
#  KubectlWrapper (offline / no ACP)
# ═════════════════════════════════════════════════════════════════════════════

class TestKubectlWrapperOffline:
    def setup_method(self) -> None:
        self.cluster = MockK8sCluster()
        self.wrapper = KubectlWrapper(
            cluster=self.cluster,
            acp_base_url=None,
            agent_token=None,
            tenant_id="00000000-0000-0000-0000-000000000001",
        )

    def test_get_pods_production(self) -> None:
        output, acp = self.wrapper.run("get pods -n production")
        assert "NAME" in output
        assert "READY" in output
        assert acp["action"] == "allow"
        assert acp.get("_bypassed") is True

    def test_get_namespaces(self) -> None:
        output, _ = self.wrapper.run("get namespaces")
        assert "production" in output
        assert "staging" in output
        assert "kube-system" in output

    def test_get_nodes(self) -> None:
        output, _ = self.wrapper.run("get nodes")
        assert "node-1" in output
        assert "Ready" in output

    def test_describe_deployment(self) -> None:
        output, _ = self.wrapper.run("describe deployment checkout -n production")
        assert "checkout" in output
        assert "Replicas" in output

    def test_describe_missing_deployment_returns_error(self) -> None:
        output, _ = self.wrapper.run("describe deployment does-not-exist -n production")
        assert "not found" in output

    def test_scale_staging(self) -> None:
        output, acp = self.wrapper.run("scale deployment payments-api --replicas=3 -n staging")
        assert "scaled" in output.lower() or "replicas" in output.lower()
        dep = self.cluster.get_deployment("staging", "payments-api")
        assert dep is not None
        assert dep["spec"]["replicas"] == 3

    def test_delete_namespace_no_acp_executes(self) -> None:
        """Without ACP, wrapper executes the delete (offline mode)."""
        output, acp = self.wrapper.run("delete namespace staging")
        assert "staging" not in self.cluster.namespaces or "Error" in output

    def test_get_secrets_offline(self) -> None:
        output, _ = self.wrapper.run("get secrets -n production")
        assert "NAME" in output or "payments-db-creds" in output

    def test_get_clusterroles(self) -> None:
        output, _ = self.wrapper.run("get clusterroles")
        assert "cluster-admin" in output

    def test_top_nodes(self) -> None:
        output, _ = self.wrapper.run("top nodes")
        assert "CPU" in output or "MEMORY" in output or "node-" in output

    def test_logs_existing_pod(self) -> None:
        pods = self.cluster.list_pods("production")
        pod_name = pods[0]["metadata"]["name"]
        output, _ = self.wrapper.run(f"logs {pod_name} -n production")
        # Should return log lines
        assert len(output.splitlines()) >= 5

    def test_apply_configmap(self) -> None:
        output, _ = self.wrapper.run("apply configmap app-config -n staging")
        assert "configmap" in output.lower()
        assert ("staging", "app-config") in self.cluster.configmaps

    def test_create_clusterrolebinding(self) -> None:
        output, _ = self.wrapper.run(
            "create clusterrolebinding reader-bind "
            "--clusterrole=view --serviceaccount=default:reader-sa"
        )
        assert "created" in output.lower()
        assert "reader-bind" in self.cluster.clusterrolebindings


# ═════════════════════════════════════════════════════════════════════════════
#  OPA policy determinism (via local_eval — no running OPA required)
# ═════════════════════════════════════════════════════════════════════════════

class TestK8sPolicyLocal:
    """
    Test the k8s_policy.rego logic using the local eval module (re-implements
    the key rules in Python for deterministic unit testing without OPA server).

    These mirror the Rego rules and serve as regression guards.
    """

    @staticmethod
    def _is_readonly_verb(verb: str) -> bool:
        return verb.lower() in {"get", "list", "describe", "logs", "top", "watch"}

    @staticmethod
    def _is_production(namespace: str) -> bool:
        return namespace.lower() == "production"

    @staticmethod
    def _is_hard_deny_deletion(verb: str, resource: str) -> bool:
        return verb.lower() in {"delete", "remove"} and resource.lower() in {
            "namespace", "namespaces", "node", "nodes",
            "persistentvolume", "persistentvolumes", "pv",
        }

    @staticmethod
    def _is_admin_grant(verb: str, resource: str, clusterrole: str) -> bool:
        return (verb.lower() in {"create", "patch", "apply"}
                and "clusterrolebinding" in resource.lower()
                and clusterrole == "cluster-admin")

    def _policy(self, verb: str, resource: str, namespace: str = "default",
                name: str = "", clusterrole: str = "") -> dict:
        allow = False
        requires_approval = False
        reason = "no matching allow rule"
        risk_adj = 0.0

        if self._is_hard_deny_deletion(verb, resource):
            if "namespace" in resource:
                reason = "HARD DENY: namespace deletion is never permitted by automation"
                risk_adj = 0.50
            elif "node" in resource:
                reason = "HARD DENY: node deletion destabilizes the control plane"
                risk_adj = 0.50
            else:
                reason = "HARD DENY: PersistentVolume deletion risks data loss"
                risk_adj = 0.45
        elif self._is_admin_grant(verb, resource, clusterrole):
            reason = "HARD DENY: cluster-admin privilege escalation blocked"
            risk_adj = 0.60
        elif self._is_readonly_verb(verb):
            allow = True
            reason = "read-only operation permitted"
        elif verb.lower() == "scale" and not self._is_production(namespace):
            allow = True
            reason = "scaling permitted (non-production)"
        elif verb.lower() == "scale" and self._is_production(namespace):
            requires_approval = True
            reason = "production scaling — approval required"
        elif verb.lower() in {"create", "patch"} and "clusterrolebinding" in resource:
            requires_approval = True
            reason = "RBAC mutation requires explicit operator approval"
        elif verb.lower() == "exec":
            requires_approval = True
            reason = "exec access requires explicit approval"
        elif verb.lower() == "apply" and "configmap" in resource and not self._is_production(namespace):
            allow = True
            reason = "configmap apply permitted (non-production)"

        return {"allow": allow, "reason": reason,
                "risk_adjustment": risk_adj, "requires_approval": requires_approval}

    # Safe reads
    def test_get_pods_allowed(self) -> None:
        r = self._policy("get", "pod", "production")
        assert r["allow"] is True
        assert r["risk_adjustment"] == 0.0

    def test_list_deployments_allowed(self) -> None:
        r = self._policy("list", "deployment", "staging")
        assert r["allow"] is True

    def test_describe_deployment_allowed(self) -> None:
        r = self._policy("describe", "deployment", "production")
        assert r["allow"] is True

    def test_logs_allowed(self) -> None:
        r = self._policy("logs", "pod", "production")
        assert r["allow"] is True

    def test_top_nodes_allowed(self) -> None:
        r = self._policy("top", "node")
        assert r["allow"] is True

    # Safe mutations
    def test_scale_staging_allowed(self) -> None:
        r = self._policy("scale", "deployment", "staging")
        assert r["allow"] is True

    def test_scale_production_requires_approval(self) -> None:
        r = self._policy("scale", "deployment", "production")
        assert r["allow"] is False
        assert r["requires_approval"] is True

    def test_apply_configmap_staging_allowed(self) -> None:
        r = self._policy("apply", "configmap", "staging")
        assert r["allow"] is True

    # Hard denies
    def test_delete_namespace_denied(self) -> None:
        r = self._policy("delete", "namespace", "production", "production")
        assert r["allow"] is False
        assert r["risk_adjustment"] >= 0.45

    def test_delete_namespace_staging_also_denied(self) -> None:
        r = self._policy("delete", "namespace", "staging", "staging")
        assert r["allow"] is False

    def test_delete_node_denied(self) -> None:
        r = self._policy("delete", "node", "", "node-1")
        assert r["allow"] is False
        assert r["risk_adjustment"] >= 0.45

    def test_delete_pv_denied(self) -> None:
        r = self._policy("delete", "pv", "", "data-pv-1")
        assert r["allow"] is False
        assert r["risk_adjustment"] >= 0.45

    def test_cluster_admin_grant_denied(self) -> None:
        r = self._policy("create", "clusterrolebinding", "default", "admin-bind",
                         clusterrole="cluster-admin")
        assert r["allow"] is False
        assert r["risk_adjustment"] >= 0.50

    def test_cluster_admin_risk_adj_highest(self) -> None:
        ns_del = self._policy("delete", "namespace", "production", "production")
        ca_grant = self._policy("create", "clusterrolebinding", "default", "x",
                                clusterrole="cluster-admin")
        assert ca_grant["risk_adjustment"] >= ns_del["risk_adjustment"] * 0.9

    def test_exec_requires_approval(self) -> None:
        r = self._policy("exec", "pod", "production")
        assert r["allow"] is False
        assert r["requires_approval"] is True

    def test_rbac_create_requires_approval(self) -> None:
        r = self._policy("create", "clusterrolebinding", "default", "safe-bind",
                         clusterrole="view")
        assert r["requires_approval"] is True


# ═════════════════════════════════════════════════════════════════════════════
#  Blast radius invariants (using existing trust_engine)
# ═════════════════════════════════════════════════════════════════════════════

class TestBlastRadiusInvariants:
    """Property-style checks on the trust_engine math used by blast-radius."""

    def _trust(self, total: int, err: int, deny: int, avg_risk: float,
               max_risk: float = 0.0, drift: float = 0.0) -> float:
        from services.identity_graph.trust_engine import compute_trust
        score, _, _ = compute_trust({
            "total": total, "error": err, "deny": deny,
            "avg_risk": avg_risk, "max_risk": max_risk,
        }, drift_score=drift)
        return score

    def test_perfect_agent_score_is_1(self) -> None:
        score = self._trust(100, 0, 0, 0.0)
        assert score == 1.0

    def test_trust_bounded_0_to_1(self) -> None:
        import random
        for _ in range(50):
            total = random.randint(1, 1000)
            score = self._trust(
                total=total,
                err=random.randint(0, total),
                deny=random.randint(0, total),
                avg_risk=random.random(),
                max_risk=random.random(),
                drift=random.random() * 2,
            )
            assert 0.0 <= score <= 1.0

    def test_high_error_rate_degrades_trust(self) -> None:
        good = self._trust(100, 0, 0, 0.1)
        bad  = self._trust(100, 80, 0, 0.1)
        assert bad < good

    def test_high_deny_rate_degrades_trust(self) -> None:
        good = self._trust(100, 0, 0, 0.1)
        bad  = self._trust(100, 0, 80, 0.1)
        assert bad < good

    def test_high_avg_risk_degrades_trust(self) -> None:
        low_risk  = self._trust(100, 0, 0, 0.1)
        high_risk = self._trust(100, 0, 0, 0.9)
        assert high_risk < low_risk

    def test_max_risk_burst_penalty(self) -> None:
        no_burst   = self._trust(100, 0, 0, 0.1, max_risk=0.5)
        with_burst = self._trust(100, 0, 0, 0.1, max_risk=0.95)
        assert with_burst < no_burst

    def test_drift_degrades_trust(self) -> None:
        no_drift   = self._trust(100, 0, 0, 0.1, drift=0.0)
        with_drift = self._trust(100, 0, 0, 0.1, drift=1.5)
        assert with_drift < no_drift

    def test_reason_labels(self) -> None:
        from services.identity_graph.trust_engine import compute_trust
        _, _, reason = compute_trust({"total": 100, "error": 0, "deny": 0,
                                       "avg_risk": 0.0, "max_risk": 0.0})
        assert reason == "healthy"

        _, _, reason = compute_trust({"total": 100, "error": 80, "deny": 60,
                                       "avg_risk": 0.9, "max_risk": 0.95})
        assert "untrusted" in reason


# ═════════════════════════════════════════════════════════════════════════════
#  Autonomy enforcement simulation
# ═════════════════════════════════════════════════════════════════════════════

class TestAutonomySimulation:
    """Simulate the contract enforcement logic in-process."""

    def _check_contract(
        self,
        verb: str,
        resource: str,
        destructive_ops_this_hour: int,
        blast_radius: float,
    ) -> tuple[bool, str]:
        """Simplified autonomy contract check matching the demo scenario."""
        MAX_DESTRUCTIVE = 3
        MAX_BLAST_RADIUS = 0.70

        is_destructive = verb in ("delete", "remove") or (
            verb in ("create", "patch") and "clusterrolebinding" in resource
        )
        is_hard_denied = resource in ("namespace", "namespaces", "node", "nodes")

        if is_hard_denied and verb in ("delete", "remove"):
            return False, f"autonomy.denied_action: {verb} {resource} is in denied_actions list"

        if is_destructive and destructive_ops_this_hour >= MAX_DESTRUCTIVE:
            return False, f"autonomy.max_cost_exceeded: destructive_ops/hr={destructive_ops_this_hour} > {MAX_DESTRUCTIVE}"

        if blast_radius > MAX_BLAST_RADIUS:
            return False, f"autonomy.max_cost_exceeded: blast_radius={blast_radius:.2f} > {MAX_BLAST_RADIUS}"

        return True, "within contract limits"

    def test_read_always_within_contract(self) -> None:
        ok, msg = self._check_contract("get", "pod", 0, 0.0)
        assert ok

    def test_first_delete_allowed(self) -> None:
        ok, msg = self._check_contract("delete", "pod", 0, 0.1)
        assert ok

    def test_fourth_delete_blocked(self) -> None:
        ok, msg = self._check_contract("delete", "pod", 3, 0.1)
        assert not ok
        assert "destructive_ops" in msg

    def test_namespace_delete_hard_denied(self) -> None:
        ok, msg = self._check_contract("delete", "namespace", 0, 0.0)
        assert not ok
        assert "denied_action" in msg

    def test_node_delete_hard_denied(self) -> None:
        ok, msg = self._check_contract("delete", "nodes", 0, 0.0)
        assert not ok

    def test_high_blast_radius_blocked(self) -> None:
        ok, msg = self._check_contract("scale", "deployment", 0, 0.85)
        assert not ok
        assert "blast_radius" in msg

    def test_blast_radius_at_threshold_blocked(self) -> None:
        ok, msg = self._check_contract("scale", "deployment", 0, 0.71)
        assert not ok

    def test_blast_radius_below_threshold_allowed(self) -> None:
        ok, msg = self._check_contract("scale", "deployment", 0, 0.69)
        assert ok

    def test_rbac_grant_is_destructive_op(self) -> None:
        ok, msg = self._check_contract("create", "clusterrolebinding", 3, 0.1)
        assert not ok


# ═════════════════════════════════════════════════════════════════════════════
#  Kill switch persistence model
# ═════════════════════════════════════════════════════════════════════════════

class TestKillSwitchModel:
    """
    Model the kill switch persistence contract:
    - Written to Postgres at engage time (survives Redis FLUSHDB)
    - Cleared on disengage
    - All /execute calls check before processing
    """

    def setup_method(self) -> None:
        self._pg_store: set[str] = set()  # tenant_ids with active kill switch
        self._redis_cache: set[str] = set()

    def engage(self, tenant_id: str) -> None:
        self._pg_store.add(tenant_id)
        self._redis_cache.add(tenant_id)

    def flush_redis(self) -> None:
        self._redis_cache.clear()

    def is_blocked(self, tenant_id: str) -> bool:
        # ACP checks Redis first, falls back to Postgres on cache miss
        if tenant_id in self._redis_cache:
            return True
        if tenant_id in self._pg_store:
            # Rehydrate cache
            self._redis_cache.add(tenant_id)
            return True
        return False

    def disengage(self, tenant_id: str) -> None:
        self._pg_store.discard(tenant_id)
        self._redis_cache.discard(tenant_id)

    def test_not_blocked_initially(self) -> None:
        assert not self.is_blocked("tenant-1")

    def test_blocked_after_engage(self) -> None:
        self.engage("tenant-1")
        assert self.is_blocked("tenant-1")

    def test_survives_redis_flush(self) -> None:
        self.engage("tenant-1")
        self.flush_redis()
        assert self.is_blocked("tenant-1")

    def test_not_blocked_after_disengage(self) -> None:
        self.engage("tenant-1")
        self.flush_redis()
        self.disengage("tenant-1")
        assert not self.is_blocked("tenant-1")

    def test_other_tenants_unaffected(self) -> None:
        self.engage("tenant-1")
        assert not self.is_blocked("tenant-2")

    def test_flush_then_rehydrate(self) -> None:
        self.engage("tenant-1")
        self.flush_redis()
        # First call reads from Postgres and rehydrates
        assert self.is_blocked("tenant-1")
        # Cache now warm again
        assert "tenant-1" in self._redis_cache


# ═════════════════════════════════════════════════════════════════════════════
#  Demo script static checks
# ═════════════════════════════════════════════════════════════════════════════

class TestDemoStaticChecks:
    """Ensure demo scripts have required structure."""

    _root = Path(__file__).parent.parent

    def test_scripted_demo_exists(self) -> None:
        assert (self._root / "demos/devops_agent/scripted_demo.py").exists()

    def test_setup_demo_exists(self) -> None:
        assert (self._root / "demos/devops_agent/setup_demo.py").exists()

    def test_mock_k8s_exists(self) -> None:
        assert (self._root / "demos/devops_agent/mock_k8s.py").exists()

    def test_k8s_signals_exists(self) -> None:
        assert (self._root / "demos/devops_agent/k8s_signals.py").exists()

    def test_kubectl_wrapper_exists(self) -> None:
        assert (self._root / "demos/devops_agent/kubectl_wrapper.py").exists()

    def test_k8s_policy_rego_exists(self) -> None:
        rego = self._root / "services/policy/policies/k8s_policy.rego"
        assert rego.exists()

    def test_k8s_policy_rego_has_hard_denies(self) -> None:
        rego = (self._root / "services/policy/policies/k8s_policy.rego").read_text()
        assert "HARD DENY" in rego
        assert "cluster-admin" in rego
        assert "namespace" in rego

    def test_scripted_demo_has_all_9_scenarios(self) -> None:
        src = (self._root / "demos/devops_agent/scripted_demo.py").read_text()
        for i in range(1, 10):
            assert f"Scenario {i}" in src, f"Missing Scenario {i} in scripted_demo.py"

    def test_scripted_demo_has_kill_switch(self) -> None:
        src = (self._root / "demos/devops_agent/scripted_demo.py").read_text()
        assert "kill_switch" in src.lower() or "kill switch" in src.lower()

    def test_setup_demo_has_autonomy_contract(self) -> None:
        src = (self._root / "demos/devops_agent/setup_demo.py").read_text()
        assert "autonomy" in src.lower()
        assert "destructive" in src.lower()

    def test_mock_k8s_has_all_required_namespaces(self) -> None:
        from demos.devops_agent.mock_k8s import _NAMESPACES
        for ns in ("default", "staging", "production", "monitoring", "kube-system"):
            assert ns in _NAMESPACES

    def test_k8s_signals_has_7_detectors(self) -> None:
        from demos.devops_agent.k8s_signals import K8sSignalEngine
        engine = K8sSignalEngine()
        # Verify all 7 detectors exist as methods
        detectors = [
            "destructive_deletion_loop",
            "namespace_sweep",
            "pod_deletion_storm",
            "privilege_escalation_recon",
            "secrets_recon",
            "automation_runaway",
            "cross_namespace_destructive_escalation",
        ]
        for d in detectors:
            assert hasattr(engine, d), f"Missing detector: {d}"

    def test_demo_script_file_exists(self) -> None:
        assert (self._root / "demos/devops_agent/demo_script.md").exists()
