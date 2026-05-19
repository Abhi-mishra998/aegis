"""
Mock Kubernetes Control Plane — in-process state store.

Behaves like a small production cluster: realistic timestamps, metadata,
status conditions, resourceVersions. Instant startup, deterministic demos.
No external dependencies, no docker required.

Namespaces: default, staging, production, monitoring, kube-system.
Resources: Namespace, Deployment, Pod, Service, ConfigMap, Secret,
           PersistentVolume, PersistentVolumeClaim, ClusterRole,
           ClusterRoleBinding, ServiceAccount, Node.
"""
from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

_BOOT_TS = datetime.now(UTC).isoformat()
_RV_COUNTER: dict[str, int] = {}


def _rv(kind: str) -> str:
    _RV_COUNTER[kind] = _RV_COUNTER.get(kind, 100) + random.randint(1, 5)
    return str(_RV_COUNTER[kind])


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _meta(name: str, namespace: str | None, kind: str, labels: dict | None = None) -> dict:
    ns_part = f"/namespaces/{namespace}" if namespace else ""
    return {
        "name": name,
        "namespace": namespace,
        "uid": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{kind}/{namespace}/{name}")),
        "resourceVersion": _rv(kind),
        "creationTimestamp": _BOOT_TS,
        "labels": labels or {},
        "annotations": {},
        "selfLink": f"/api/v1{ns_part}/{kind.lower()}s/{name}",
        "generation": 1,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Initial cluster state — realistic but small enough for laptop demos
# ─────────────────────────────────────────────────────────────────────────────

_NAMESPACES = ["default", "staging", "production", "monitoring", "kube-system"]

_NODES_SEED = [
    {"name": "node-1", "role": "control-plane", "cpu": "4", "memory": "8Gi", "ready": True},
    {"name": "node-2", "role": "worker",        "cpu": "8", "memory": "16Gi", "ready": True},
    {"name": "node-3", "role": "worker",        "cpu": "8", "memory": "16Gi", "ready": True},
]

_DEPLOYMENTS_SEED = [
    # (namespace, name, replicas, image)
    ("production", "payments-api",   3, "payments:v2.1.0"),
    ("production", "checkout",       2, "checkout:v1.8.3"),
    ("production", "auth-service",   2, "auth:v3.0.1"),
    ("staging",    "payments-api",   1, "payments:v2.2.0-rc1"),
    ("staging",    "checkout",       1, "checkout:v1.9.0-beta"),
    ("staging",    "feature-flags",  1, "flags:v0.3.0"),
    ("default",    "demo-app",       1, "nginx:1.25"),
    ("monitoring", "prometheus",     1, "prom/prometheus:v2.48"),
    ("monitoring", "grafana",        1, "grafana/grafana:10.2"),
    ("kube-system","coredns",        2, "registry.k8s.io/coredns:v1.11.1"),
]

_SECRETS_SEED = [
    ("production", "payments-db-creds",  "Opaque"),
    ("production", "stripe-api-key",     "Opaque"),
    ("production", "tls-cert",           "kubernetes.io/tls"),
    ("staging",    "staging-db-creds",   "Opaque"),
    ("staging",    "staging-jwt-secret", "Opaque"),
    ("kube-system","admin-kubeconfig",   "kubernetes.io/service-account-token"),
]

_CLUSTERROLES_SEED = [
    "cluster-admin",
    "view",
    "edit",
    "payments-reader",
    "monitoring-reader",
]

_SERVICE_ACCOUNTS_SEED = [
    ("production", "payments-sa"),
    ("production", "checkout-sa"),
    ("staging",    "staging-deployer"),
    ("kube-system","default"),
]


def _build_namespace(name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": _meta(name, None, "Namespace",
                          {"kubernetes.io/metadata.name": name}),
        "spec": {"finalizers": ["kubernetes"]},
        "status": {"phase": "Active"},
    }


def _build_node(seed: dict) -> dict:
    name = seed["name"]
    labels = {
        "kubernetes.io/hostname": name,
        "kubernetes.io/os": "linux",
        "node-role.kubernetes.io/worker": "" if seed["role"] == "worker" else None,
    }
    labels = {k: v for k, v in labels.items() if v is not None}
    if seed["role"] == "control-plane":
        labels["node-role.kubernetes.io/control-plane"] = ""
    return {
        "apiVersion": "v1",
        "kind": "Node",
        "metadata": _meta(name, None, "Node", labels),
        "spec": {"podCIDR": f"10.{random.randint(1,254)}.0.0/24"},
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if seed["ready"] else "False",
                             "lastTransitionTime": _BOOT_TS}],
            "capacity": {"cpu": seed["cpu"], "memory": seed["memory"]},
            "allocatable": {"cpu": seed["cpu"], "memory": seed["memory"]},
            "nodeInfo": {
                "kubeletVersion": "v1.28.4",
                "osImage": "Ubuntu 22.04.3 LTS",
                "architecture": "amd64",
            },
        },
    }


def _build_deployment(ns: str, name: str, replicas: int, image: str) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": _meta(name, ns, "Deployment",
                          {"app": name.replace("-", "_")}),
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [{"name": name, "image": image,
                                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"},
                                                  "limits":   {"cpu": "500m", "memory": "512Mi"}}}],
                    "serviceAccountName": f"{name[:8]}-sa" if ns == "production" else "default",
                },
            },
            "strategy": {"type": "RollingUpdate",
                          "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0}},
        },
        "status": {
            "replicas": replicas,
            "readyReplicas": replicas,
            "availableReplicas": replicas,
            "conditions": [{"type": "Available", "status": "True",
                             "lastTransitionTime": _BOOT_TS}],
        },
    }


def _build_pods(ns: str, deploy_name: str, replicas: int, image: str) -> list[dict]:
    pods = []
    for _i in range(replicas):
        suffix = uuid.uuid4().hex[:5]
        pname = f"{deploy_name}-{uuid.uuid4().hex[:8]}-{suffix}"
        pods.append({
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": _meta(pname, ns, "Pod",
                               {"app": deploy_name,
                                "pod-template-hash": suffix}),
            "spec": {
                "nodeName": random.choice([n["name"] for n in _NODES_SEED]),
                "containers": [{"name": deploy_name, "image": image}],
            },
            "status": {
                "phase": "Running",
                "podIP": f"10.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}",
                "conditions": [{"type": "Ready", "status": "True"}],
                "containerStatuses": [{
                    "name": deploy_name,
                    "image": image,
                    "ready": True,
                    "restartCount": 0,
                    "state": {"running": {"startedAt": _BOOT_TS}},
                }],
            },
        })
    return pods


def _build_secret(ns: str, name: str, secret_type: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": _meta(name, ns, "Secret"),
        "type": secret_type,
        "data": {"key": "PHJlZGFjdGVkPg=="},  # <redacted> base64
    }


def _build_service(ns: str, name: str, port: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": _meta(name, ns, "Service", {"app": name}),
        "spec": {
            "selector": {"app": name},
            "ports": [{"port": port, "targetPort": port, "protocol": "TCP"}],
            "type": "ClusterIP",
            "clusterIP": f"10.96.{random.randint(1,254)}.{random.randint(1,254)}",
        },
        "status": {"loadBalancer": {}},
    }


def _build_clusterrole(name: str) -> dict:
    rules: list = []
    if name == "cluster-admin":
        rules = [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]
    elif name == "view":
        rules = [{"apiGroups": [""], "resources": ["pods", "services", "deployments"],
                  "verbs": ["get", "list", "watch"]}]
    elif name == "edit":
        rules = [{"apiGroups": [""], "resources": ["*"],
                  "verbs": ["get", "list", "watch", "create", "update", "patch", "delete"]}]
    else:
        rules = [{"apiGroups": [""], "resources": ["pods", "services"],
                  "verbs": ["get", "list"]}]
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": _meta(name, None, "ClusterRole"),
        "rules": rules,
    }


def _build_service_account(ns: str, name: str) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": _meta(name, ns, "ServiceAccount"),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Cluster state — mutable, persists across the demo session
# ─────────────────────────────────────────────────────────────────────────────

class MockK8sCluster:
    """In-process mutable K8s cluster state."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.namespaces: dict[str, dict] = {
            ns: _build_namespace(ns) for ns in _NAMESPACES
        }
        self.nodes: dict[str, dict] = {
            n["name"]: _build_node(n) for n in _NODES_SEED
        }
        self.deployments: dict[tuple, dict] = {}
        self.pods: dict[str, dict] = {}          # key = pod name
        self.services: dict[tuple, dict] = {}
        self.secrets: dict[tuple, dict] = {}
        self.configmaps: dict[tuple, dict] = {}
        self.clusterroles: dict[str, dict] = {
            name: _build_clusterrole(name) for name in _CLUSTERROLES_SEED
        }
        self.clusterrolebindings: dict[str, dict] = {}
        self.serviceaccounts: dict[tuple, dict] = {}

        for ns, name, replicas, image in _DEPLOYMENTS_SEED:
            self.deployments[(ns, name)] = _build_deployment(ns, name, replicas, image)
            for pod in _build_pods(ns, name, replicas, image):
                self.pods[pod["metadata"]["name"]] = pod
            self.services[(ns, name)] = _build_service(ns, name, 8080)

        for ns, name, stype in _SECRETS_SEED:
            self.secrets[(ns, name)] = _build_secret(ns, name, stype)

        for ns, name in _SERVICE_ACCOUNTS_SEED:
            self.serviceaccounts[(ns, name)] = _build_service_account(ns, name)

    # ── Read helpers ──────────────────────────────────────────────────────────

    def list_pods(self, namespace: str | None = None) -> list[dict]:
        pods = list(self.pods.values())
        if namespace:
            pods = [p for p in pods if p["metadata"]["namespace"] == namespace]
        return pods

    def list_deployments(self, namespace: str | None = None) -> list[dict]:
        deps = list(self.deployments.values())
        if namespace:
            deps = [d for d in deps if d["metadata"]["namespace"] == namespace]
        return deps

    def list_namespaces(self) -> list[dict]:
        return list(self.namespaces.values())

    def list_nodes(self) -> list[dict]:
        return list(self.nodes.values())

    def list_secrets(self, namespace: str | None = None) -> list[dict]:
        secrets = list(self.secrets.values())
        if namespace:
            secrets = [s for s in secrets if s["metadata"]["namespace"] == namespace]
        return secrets

    def list_clusterroles(self) -> list[dict]:
        return list(self.clusterroles.values())

    def get_deployment(self, namespace: str, name: str) -> dict | None:
        return self.deployments.get((namespace, name))

    def get_logs(self, namespace: str, pod_name: str, lines: int = 20) -> str:
        pod = self.pods.get(pod_name)
        if pod and pod["metadata"]["namespace"] == namespace:
            app = pod["metadata"]["labels"].get("app", pod_name)
            logs = []
            for _i in range(lines):
                ts = _now()
                levels = ["INFO", "INFO", "INFO", "WARN", "DEBUG"]
                level = random.choice(levels)
                msgs = [
                    f"Handling request POST /api/v1/checkout id={uuid.uuid4().hex[:8]}",
                    "DB query latency=12ms rows=1",
                    f"Cache HIT key=session:{uuid.uuid4().hex[:8]}",
                    "Health check /healthz → 200 OK",
                    f"Rate limit check tenant={uuid.uuid4().hex[:8]} → ok",
                    "Payment processed amount=99.99 currency=USD",
                ]
                logs.append(f"{ts} [{level}] {app}: {random.choice(msgs)}")
            return "\n".join(logs)
        return f"Error: pod {pod_name} not found in namespace {namespace}"

    # ── Write helpers (called only after ACP allows) ──────────────────────────

    def scale_deployment(self, namespace: str, name: str, replicas: int) -> dict | str:
        key = (namespace, name)
        if key not in self.deployments:
            return f"Error: deployment {name} not found in namespace {namespace}"
        self.deployments[key]["spec"]["replicas"] = replicas
        self.deployments[key]["status"]["replicas"] = replicas
        self.deployments[key]["status"]["readyReplicas"] = replicas
        self.deployments[key]["metadata"]["resourceVersion"] = _rv("Deployment")
        return {"message": f"deployment.apps/{name} scaled", "replicas": replicas}

    def delete_namespace(self, name: str) -> dict | str:
        if name not in self.namespaces:
            return f"Error: namespace {name} not found"
        del self.namespaces[name]
        # cascade-remove resources
        self.deployments = {k: v for k, v in self.deployments.items() if k[0] != name}
        self.pods = {k: v for k, v in self.pods.items()
                     if v["metadata"]["namespace"] != name}
        self.secrets = {k: v for k, v in self.secrets.items() if k[0] != name}
        return {"message": f'namespace "{name}" deleted'}

    def delete_node(self, name: str) -> dict | str:
        if name not in self.nodes:
            return f"Error: node {name} not found"
        del self.nodes[name]
        return {"message": f'node "{name}" deleted'}

    def create_clusterrolebinding(self, name: str, role: str, subject: str) -> dict:
        obj = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": _meta(name, None, "ClusterRoleBinding"),
            "roleRef": {"apiGroup": "rbac.authorization.k8s.io",
                        "kind": "ClusterRole", "name": role},
            "subjects": [{"kind": "ServiceAccount", "name": subject,
                           "namespace": "default"}],
        }
        self.clusterrolebindings[name] = obj
        return obj

    def apply_configmap(self, namespace: str, name: str, data: dict) -> dict:
        obj = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": _meta(name, namespace, "ConfigMap"),
            "data": data,
        }
        self.configmaps[(namespace, name)] = obj
        return obj

    # ── kubectl-style formatted output ────────────────────────────────────────

    @staticmethod
    def _tabulate(headers: list[str], rows: list[list[str]]) -> str:
        widths = [max(len(h), max((len(r[i]) for r in rows), default=0))
                  for i, h in enumerate(headers)]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        lines = [fmt.format(*headers)]
        for row in rows:
            lines.append(fmt.format(*row))
        return "\n".join(lines)

    def format_pods(self, pods: list[dict]) -> str:
        if not pods:
            return "No resources found."
        rows = []
        for p in pods:
            name = p["metadata"]["name"]
            ns = p["metadata"]["namespace"]
            phase = p["status"]["phase"]
            ready = p["status"]["containerStatuses"][0]["ready"]
            restarts = p["status"]["containerStatuses"][0]["restartCount"]
            age = "2d"
            rows.append([name, ns, "1/1" if ready else "0/1", phase,
                          str(restarts), age])
        return self._tabulate(
            ["NAME", "NAMESPACE", "READY", "STATUS", "RESTARTS", "AGE"], rows
        )

    def format_deployments(self, deps: list[dict]) -> str:
        if not deps:
            return "No resources found."
        rows = []
        for d in deps:
            name = d["metadata"]["name"]
            ns = d["metadata"]["namespace"]
            ready = d["status"]["readyReplicas"]
            total = d["spec"]["replicas"]
            rows.append([name, ns, f"{ready}/{total}", str(total), str(ready), "2d"])
        return self._tabulate(
            ["NAME", "NAMESPACE", "READY", "UP-TO-DATE", "AVAILABLE", "AGE"], rows
        )

    def format_namespaces(self, nss: list[dict]) -> str:
        rows = [[n["metadata"]["name"], n["status"]["phase"], "Active", "2d"]
                for n in nss]
        return self._tabulate(["NAME", "STATUS", "PHASE", "AGE"], rows)

    def format_nodes(self, nodes: list[dict]) -> str:
        rows = []
        for n in nodes:
            ready = any(c["status"] == "True" and c["type"] == "Ready"
                        for c in n["status"]["conditions"])
            roles = ",".join(k.replace("node-role.kubernetes.io/", "")
                              for k in n["metadata"]["labels"]
                              if "node-role" in k) or "worker"
            cpu = n["status"]["capacity"]["cpu"]
            mem = n["status"]["capacity"]["memory"]
            rows.append([n["metadata"]["name"], "Ready" if ready else "NotReady",
                          roles, "v1.28.4", cpu, mem])
        return self._tabulate(["NAME", "STATUS", "ROLES", "VERSION", "CPU", "MEMORY"], rows)

    def format_secrets(self, secrets: list[dict]) -> str:
        rows = [[s["metadata"]["name"], s["metadata"]["namespace"],
                  s["type"], "1", "2d"]
                for s in secrets]
        return self._tabulate(["NAME", "NAMESPACE", "TYPE", "DATA", "AGE"], rows)

    def format_clusterroles(self, roles: list[dict]) -> str:
        rows = [[r["metadata"]["name"], "2d"] for r in roles]
        return self._tabulate(["NAME", "AGE"], rows)


# module-level singleton — shared across the demo session
cluster = MockK8sCluster()
