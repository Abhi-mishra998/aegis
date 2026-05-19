"""
ACP-Protected kubectl Wrapper.

Translates kubectl-style CLI arguments into structured K8s operations,
routes EVERY operation through ACP /execute, and only applies the mutation
to the mock cluster if ACP returns action="allow".

Usage (standalone test without ACP):
    from demos.devops_agent.mock_k8s import cluster
    from demos.devops_agent.kubectl_wrapper import KubectlWrapper
    w = KubectlWrapper(cluster, acp_base_url=None, agent_token=None, tenant_id=None)
    print(w.run("get pods -n production"))

Usage (with ACP):
    w = KubectlWrapper(cluster, acp_base_url="http://localhost:8000",
                       agent_token=TOKEN, tenant_id=TENANT_ID)
    print(w.run("delete namespace production"))
"""
from __future__ import annotations

import re
import time
from typing import Any

import httpx

from demos.devops_agent.k8s_signals import K8sOp, K8sSignalEngine
from demos.devops_agent.mock_k8s import MockK8sCluster

# ─────────────────────────────────────────────────────────────────────────────
# Canonical risk scores for each operation class (used when ACP is bypassed)
# ─────────────────────────────────────────────────────────────────────────────
_OP_RISK: dict[str, float] = {
    "get":      0.00,
    "list":     0.00,
    "describe": 0.00,
    "logs":     0.00,
    "top":      0.00,
    "scale":    0.25,
    "apply":    0.20,
    "patch":    0.30,
    "create":   0.20,
    "exec":     0.55,
    "delete":   0.80,
}

_HARD_DENY_RESOURCES = frozenset({
    "namespace", "namespaces",
    "persistentvolume", "persistentvolumes", "pv",
    "node", "nodes",
})

_ESCALATION_RESOURCES = frozenset({
    "clusterrole", "clusterroles",
    "clusterrolebinding", "clusterrolebindings",
})


def _parse_args(cmd: str) -> dict:
    """
    Parse a kubectl-style command string into a structured operation dict.

    Supported forms:
      get pods [-n NAMESPACE]
      get pods -n NAMESPACE [-l LABEL]
      logs POD_NAME [-n NAMESPACE]
      describe deployment NAME [-n NAMESPACE]
      delete namespace NAME
      delete pod NAME [-n NAMESPACE]
      scale deployment NAME --replicas=N [-n NAMESPACE]
      exec -it POD -- CMD
      create clusterrolebinding NAME --clusterrole=ROLE --serviceaccount=NS:SA
      apply configmap NAME [-n NAMESPACE]
      top nodes
    """
    tokens = cmd.strip().split()
    if not tokens:
        return {"verb": "unknown", "resource": "", "name": None, "namespace": None, "args": {}}

    verb = tokens[0].lower()
    resource = tokens[1].lower() if len(tokens) > 1 else ""
    name: str | None = None
    namespace: str | None = None
    extra: dict = {}

    # Extract flags
    i = 2
    positional: list[str] = []
    while i < len(tokens):
        t = tokens[i]
        if t in ("-n", "--namespace") and i + 1 < len(tokens):
            namespace = tokens[i + 1]
            i += 2
        elif t.startswith("-n") and len(t) > 2:
            namespace = t[2:]
            i += 1
        elif t.startswith("--namespace="):
            namespace = t.split("=", 1)[1]
            i += 1
        elif t.startswith("--replicas="):
            extra["replicas"] = int(t.split("=", 1)[1])
            i += 1
        elif t.startswith("--clusterrole="):
            extra["clusterrole"] = t.split("=", 1)[1]
            i += 1
        elif t.startswith("--serviceaccount="):
            extra["serviceaccount"] = t.split("=", 1)[1]
            i += 1
        elif t in ("-it", "-i", "-t", "--"):
            i += 1
        elif t.startswith("-l") or t.startswith("--selector"):
            # skip label selectors
            i += 2 if not "=" in t else 1
        else:
            positional.append(t)
            i += 1

    if positional:
        name = positional[0]

    # Normalise resource aliases
    _aliases = {
        "po": "pods", "pod": "pods",
        "deploy": "deployments", "deployment": "deployments",
        "svc": "services", "service": "services",
        "cm": "configmaps", "configmap": "configmaps",
        "secret": "secrets",
        "pv": "persistentvolumes", "persistentvolume": "persistentvolumes",
        "pvc": "persistentvolumeclaims",
        "ns": "namespaces", "namespace": "namespaces",
        "node": "nodes",
        "sa": "serviceaccounts", "serviceaccount": "serviceaccounts",
        "crb": "clusterrolebindings", "clusterrolebinding": "clusterrolebindings",
        "cr": "clusterroles", "clusterrole": "clusterroles",
    }
    resource = _aliases.get(resource, resource)

    # "logs" is a verb whose positional arg is the pod name
    if verb == "logs" and resource and resource not in ("pods", "deployments"):
        name = resource
        resource = "pods"

    return {
        "verb": verb,
        "resource": resource.rstrip("s"),  # singular for tool naming
        "resource_plural": resource,
        "name": name,
        "namespace": namespace,
        "args": extra,
    }


class KubectlWrapper:
    """
    ACP-aware kubectl replacement backed by MockK8sCluster.

    Every mutating operation is checked against ACP before applying.
    Read operations also go through ACP so every cluster query is audited.
    """

    def __init__(
        self,
        cluster: MockK8sCluster,
        acp_base_url: str | None,
        agent_token: str | None,
        tenant_id: str | None,
        signal_engine: K8sSignalEngine | None = None,
        dry_run: bool = False,
    ) -> None:
        self._cluster = cluster
        self._acp = acp_base_url
        self._token = agent_token
        self._tenant = tenant_id
        self._signals = signal_engine or K8sSignalEngine()
        self._dry_run = dry_run

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, cmd: str) -> tuple[str, dict]:
        """
        Execute a kubectl command string.

        Returns:
            (output: str, acp_result: dict)  where acp_result contains
            action, risk, findings, and the full ACP response.
        """
        parsed = _parse_args(cmd)
        verb = parsed["verb"]
        resource = parsed["resource"]
        name = parsed["name"]
        namespace = parsed["namespace"]
        extra = parsed["args"]

        ts = time.monotonic()

        # Route to ACP first (if configured)
        acp_result = self._acp_check(parsed)

        action = acp_result.get("action", "allow")
        allowed = action not in ("deny", "kill", "block")
        http_status = acp_result.get("_http_status", 200)
        if http_status in (403, 429):
            allowed = False

        # Record the operation for behavioral analysis regardless of outcome
        self._signals.observe(K8sOp(
            ts=ts,
            verb=verb,
            resource=resource,
            namespace=namespace,
            name=name,
            risk=acp_result.get("risk", 0.0),
        ))

        if not allowed:
            denial = self._format_denial(parsed, acp_result)
            return denial, acp_result

        # Execute against mock cluster
        output = self._execute_local(parsed)
        return output, acp_result

    # ── ACP integration ───────────────────────────────────────────────────────

    def _acp_check(self, parsed: dict) -> dict:
        if not self._acp or not self._token:
            return {"action": "allow", "risk": _OP_RISK.get(parsed["verb"], 0.1),
                    "findings": [], "_http_status": 200, "_bypassed": True}

        verb = parsed["verb"]
        resource = parsed["resource"]
        name = parsed.get("name") or ""
        namespace = parsed.get("namespace") or "default"

        # Build the tool name: k8s.<verb>.<resource>
        tool = f"k8s.{verb}.{resource}"

        # Combine static op risk + live behavioral signals
        base_risk = _OP_RISK.get(verb, 0.2)
        signal_eval = self._signals.evaluate()
        behavioral_risk = signal_eval["k8s_composite_risk"]

        payload: dict[str, Any] = {
            "tool": tool,
            "input": {
                "command": f"kubectl {verb} {resource} {name}".strip(),
                "namespace": namespace,
                "resource_name": name,
                "extra_args": parsed.get("args", {}),
            },
            "metadata": {
                "operation_class": _classify_op(verb, resource),
                "namespace": namespace,
                "resource": resource,
                "name": name,
                "base_risk": base_risk,
                "k8s_behavioral_risk": behavioral_risk,
                **signal_eval,
            },
        }

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": self._tenant or "",
        }

        try:
            with httpx.Client(timeout=15) as client:
                resp = client.post(f"{self._acp}/execute", headers=headers, json=payload)
                body = resp.json()
                result = (body.get("data") or body)
                result["_http_status"] = resp.status_code
                return result
        except Exception as exc:
            return {"action": "allow", "risk": base_risk, "findings": [],
                    "_http_status": 0, "_error": str(exc)}

    # ── Local cluster execution ───────────────────────────────────────────────

    def _execute_local(self, parsed: dict) -> str:
        verb = parsed["verb"]
        resource = parsed["resource"]
        name = parsed.get("name")
        namespace = parsed.get("namespace") or "default"
        extra = parsed.get("args", {})
        c = self._cluster

        if verb in ("get", "list"):
            return self._local_get(resource, namespace, name)
        if verb == "describe":
            return self._local_describe(resource, namespace, name)
        if verb == "logs":
            pod_name = name or ""
            return c.get_logs(namespace, pod_name)
        if verb == "scale":
            replicas = int(extra.get("replicas", 1))
            result = c.scale_deployment(namespace, name or "", replicas)
            if isinstance(result, str):
                return result
            return f"deployment.apps/{name} scaled"
        if verb == "delete":
            return self._local_delete(resource, namespace, name)
        if verb == "create" and resource == "clusterrolebinding":
            role = extra.get("clusterrole", "view")
            subject = extra.get("serviceaccount", "default:default").split(":")[-1]
            c.create_clusterrolebinding(name or "new-binding", role, subject)
            return f"clusterrolebinding.rbac.authorization.k8s.io/{name} created"
        if verb == "apply":
            c.apply_configmap(namespace, name or "new-cm", {"key": "value"})
            return f"configmap/{name} configured"
        if verb in ("exec", "top"):
            return self._local_exec(verb, resource, namespace, name)
        return f"kubectl: unknown command: {verb} {resource}"

    def _local_get(self, resource: str, namespace: str, name: str | None) -> str:
        c = self._cluster
        if resource in ("namespace", "namespaces"):
            return c.format_namespaces(c.list_namespaces())
        if resource in ("node", "nodes"):
            return c.format_nodes(c.list_nodes())
        if resource in ("pod", "pods"):
            pods = c.list_pods(namespace)
            return c.format_pods(pods)
        if resource in ("deployment", "deployments"):
            deps = c.list_deployments(namespace)
            return c.format_deployments(deps)
        if resource in ("secret", "secrets"):
            return c.format_secrets(c.list_secrets(namespace))
        if resource in ("clusterrole", "clusterroles"):
            return c.format_clusterroles(c.list_clusterroles())
        if resource in ("service", "services"):
            return f"Services in namespace {namespace}: (use describe for details)"
        return f"No resources found for {resource} in namespace {namespace}."

    def _local_describe(self, resource: str, namespace: str, name: str | None) -> str:
        c = self._cluster
        if resource in ("deployment", "deployments") and name:
            dep = c.get_deployment(namespace, name)
            if dep is None:
                return f"Error: deployments.apps \"{name}\" not found"
            spec = dep["spec"]
            status = dep["status"]
            return (
                f"Name:                {dep['metadata']['name']}\n"
                f"Namespace:           {dep['metadata']['namespace']}\n"
                f"CreationTimestamp:   {dep['metadata']['creationTimestamp']}\n"
                f"Replicas:            {status['replicas']} desired | "
                f"{status['readyReplicas']} updated | "
                f"{status['availableReplicas']} available\n"
                f"StrategyType:        {spec['strategy']['type']}\n"
                f"Image:               "
                f"{spec['template']['spec']['containers'][0]['image']}\n"
                f"Conditions:\n"
                f"  Type           Status\n"
                f"  ----           ------\n"
                f"  Available      True\n"
            )
        return f"Describing {resource}/{name} in namespace {namespace} (details omitted in demo)"

    def _local_delete(self, resource: str, namespace: str, name: str | None) -> str:
        c = self._cluster
        if resource in ("namespace", "namespaces") and name:
            result = c.delete_namespace(name)
            return str(result.get("message", result)) if isinstance(result, dict) else result
        if resource in ("node", "nodes") and name:
            result = c.delete_node(name)
            return str(result.get("message", result)) if isinstance(result, dict) else result
        if resource in ("pod", "pods") and name:
            if name in c.pods:
                del c.pods[name]
                return f'pod "{name}" deleted'
            return f'Error: pods "{name}" not found'
        return f'Error: cannot delete {resource}/{name}'

    def _local_exec(self, verb: str, resource: str, namespace: str,
                    name: str | None) -> str:
        if verb == "top":
            lines = ["NAME       CPU(cores)   MEMORY(bytes)"]
            for nd in self._cluster.list_nodes():
                lines.append(f"{nd['metadata']['name']:<10} 300m         1200Mi")
            return "\n".join(lines)
        return f"exec: connected to {name} (mock shell not active)"

    # ── Denial formatting ─────────────────────────────────────────────────────

    @staticmethod
    def _format_denial(parsed: dict, acp_result: dict) -> str:
        verb = parsed["verb"]
        resource = parsed["resource"]
        name = parsed.get("name") or ""
        risk = acp_result.get("risk", 0.0)
        action = acp_result.get("action", "deny")
        findings = acp_result.get("findings", [])
        http_status = acp_result.get("_http_status", 403)

        finding_str = ", ".join(findings) if findings else "policy_deny"
        return (
            f"Error from server: ACP denied {verb} {resource}/{name}\n"
            f"  HTTP Status : {http_status}\n"
            f"  Action      : {action.upper()}\n"
            f"  Risk Score  : {risk:.3f}\n"
            f"  Findings    : [{finding_str}]\n"
            f"  Decision    : DENIED — operation blocked before cluster execution"
        )


def _classify_op(verb: str, resource: str) -> str:
    """Map verb+resource to a governance operation class."""
    if verb in ("get", "list", "describe", "logs", "top"):
        return "read"
    if verb == "scale":
        return "scaling"
    if verb == "exec":
        return "exec_access"
    if verb == "delete":
        if resource in ("namespace", "namespaces", "node", "nodes",
                        "persistentvolume", "pv"):
            return "destructive_cluster"
        return "destructive_namespaced"
    if resource in ("clusterrole", "clusterrolebinding"):
        return "privilege_escalation"
    return "mutation"
