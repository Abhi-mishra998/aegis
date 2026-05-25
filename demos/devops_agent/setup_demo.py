#!/usr/bin/env python3
"""
ACP DevOps Agent Governance demo setup.

Provisions:
  1. A demo agent (devops-agent-demo) with K8s tool permissions
  2. An autonomy contract limiting destructive operations
  3. Identity graph seeded with realistic K8s resource topology
  4. Credentials saved to .demo_creds.json

Usage:
    .venv/bin/python demos/devops_agent/setup_demo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

GATEWAY      = os.getenv("ACP_GATEWAY_URL",  "http://localhost:8000")
IDENTITY_URL = os.getenv("ACP_IDENTITY_URL", "http://localhost:8002")
GRAPH_URL    = os.getenv("ACP_GRAPH_URL",    "http://localhost:8013")
AUTONOMY_URL = os.getenv("ACP_AUTONOMY_URL", "http://localhost:8015")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "acp_internal_prod_secret_f93284h")
TENANT_ID    = os.getenv("ACP_TENANT_ID",    "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL  = os.getenv("ACP_ADMIN_EMAIL",  "admin@acp.local")
ADMIN_PASSWORD = os.getenv("ACP_ADMIN_PASSWORD", "password")

_CREDS_FILE = Path(__file__).parent / ".demo_creds.json"

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_CYAN  = "\033[96m"
_DIM   = "\033[2m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {msg}")


def _section(title: str) -> None:
    print(f"\n{_BOLD}[{title}]{_RESET}")


# ── K8s Identity Graph topology ───────────────────────────────────────────────

_K8S_NODES = [
    # (node_type, external_id, name, attributes)
    ("agent",        "devops-agent-1",     "DevOps Agent",
     {"role": "automation", "risk_level": "medium", "kind": "k8s_agent"}),

    # Namespaces
    ("k8s_namespace", "ns/production",     "production",
     {"phase": "Active", "critical": True}),
    ("k8s_namespace", "ns/staging",        "staging",
     {"phase": "Active", "critical": False}),
    ("k8s_namespace", "ns/default",        "default",
     {"phase": "Active", "critical": False}),
    ("k8s_namespace", "ns/kube-system",    "kube-system",
     {"phase": "Active", "critical": True}),
    ("k8s_namespace", "ns/monitoring",     "monitoring",
     {"phase": "Active", "critical": False}),

    # Deployments
    ("k8s_deployment", "deploy/production/payments-api", "payments-api",
     {"namespace": "production", "replicas": 3, "critical": True}),
    ("k8s_deployment", "deploy/production/checkout",     "checkout",
     {"namespace": "production", "replicas": 2, "critical": True}),
    ("k8s_deployment", "deploy/production/auth-service", "auth-service",
     {"namespace": "production", "replicas": 2, "critical": True}),
    ("k8s_deployment", "deploy/staging/payments-api",    "payments-api-staging",
     {"namespace": "staging", "replicas": 1, "critical": False}),

    # Secrets (high-value targets)
    ("k8s_secret",    "secret/production/payments-db-creds", "payments-db-creds",
     {"namespace": "production", "type": "Opaque", "critical": True}),
    ("k8s_secret",    "secret/production/stripe-api-key",    "stripe-api-key",
     {"namespace": "production", "type": "Opaque", "critical": True}),
    ("k8s_secret",    "secret/kube-system/admin-kubeconfig", "admin-kubeconfig",
     {"namespace": "kube-system", "type": "service-account-token", "critical": True}),

    # Databases (downstream of deployments)
    ("resource",      "db/payments-db",    "payments-db",
     {"kind": "database", "engine": "postgres", "critical": True}),
    ("resource",      "db/auth-db",        "auth-db",
     {"kind": "database", "engine": "postgres", "critical": True}),

    # Nodes (infrastructure)
    ("k8s_node",      "node/node-1",       "node-1",
     {"role": "control-plane", "cpu": "4", "critical": True}),
    ("k8s_node",      "node/node-2",       "node-2",
     {"role": "worker", "cpu": "8", "critical": False}),
    ("k8s_node",      "node/node-3",       "node-3",
     {"role": "worker", "cpu": "8", "critical": False}),

    # RBAC
    ("k8s_rbac",      "cr/cluster-admin",  "cluster-admin",
     {"kind": "ClusterRole", "power": "unrestricted", "critical": True}),
    ("k8s_rbac",      "sa/production/payments-sa", "payments-sa",
     {"kind": "ServiceAccount", "namespace": "production"}),
]

_K8S_EDGES = [
    # (src_external_id, dst_external_id, edge_type, action)
    ("devops-agent-1",      "ns/production",              "invokes", "list_namespace"),
    ("devops-agent-1",      "ns/staging",                 "invokes", "list_namespace"),
    ("devops-agent-1",      "deploy/production/payments-api", "invokes", "scale_deployment"),
    ("devops-agent-1",      "secret/production/stripe-api-key", "reads", "get_secret"),
    ("devops-agent-1",      "cr/cluster-admin",           "escalates", "create_binding"),
    ("deploy/production/payments-api", "db/payments-db",  "reads", "connect"),
    ("deploy/production/auth-service", "db/auth-db",      "reads", "connect"),
    ("secret/production/payments-db-creds", "db/payments-db", "reads", "authenticate"),
    ("secret/production/stripe-api-key",    "deploy/production/payments-api", "reads", "configure"),
    ("cr/cluster-admin",    "ns/production",              "escalates", "full_access"),
    ("cr/cluster-admin",    "ns/kube-system",             "escalates", "full_access"),
    ("sa/production/payments-sa", "secret/production/payments-db-creds", "reads", "mount"),
    ("deploy/production/payments-api", "ns/production",   "invokes", "pod_exec"),
    ("node/node-1",         "ns/kube-system",             "invokes", "host_access"),
]


async def _upsert_node(
    client: httpx.AsyncClient,
    headers: dict,
    node_type: str,
    external_id: str,
    name: str,
    attributes: dict,
) -> str | None:
    """Upsert a graph node and return its UUID (or None on failure)."""
    resp = await client.post(
        f"{GRAPH_URL}/graph/nodes",
        headers=headers,
        json={
            "node_type": node_type,
            "external_id": external_id,
            "name": name,
            "attributes": attributes,
        },
        timeout=10,
    )
    if resp.status_code in (200, 201):
        data = (resp.json().get("data") or resp.json())
        return data.get("id")
    if resp.status_code == 404:
        # Endpoint may not support direct node creation — skip silently
        return None
    return None


async def _seed_identity_graph(client: httpx.AsyncClient, headers: dict) -> dict[str, str]:
    """
    Seed the identity graph with K8s resource topology.

    Returns a map of external_id → node UUID (best-effort; missing nodes are
    skipped so the rest of setup still works).
    """
    _section("Seeding Identity Graph")
    # Check if the graph service supports direct node creation
    probe = await client.get(f"{GRAPH_URL}/graph/agents", headers=headers, timeout=5)
    if probe.status_code not in (200, 201):
        print(f"  {_DIM}Identity graph not reachable (HTTP {probe.status_code}) — skipping seed{_RESET}")
        return {}

    id_map: dict[str, str] = {}
    for node_type, ext_id, name, attrs in _K8S_NODES:
        node_id = await _upsert_node(client, headers, node_type, ext_id, name, attrs)
        if node_id:
            id_map[ext_id] = node_id

    if id_map:
        _ok(f"Seeded {len(id_map)} identity graph nodes")
    else:
        print(f"  {_DIM}Graph node creation endpoint not available — graph pre-populated by live calls{_RESET}")

    # Seed edges using node UUID map
    edge_count = 0
    for src_ext, dst_ext, edge_type, action in _K8S_EDGES:
        src_id = id_map.get(src_ext)
        dst_id = id_map.get(dst_ext)
        if not src_id or not dst_id:
            continue
        resp = await client.post(
            f"{GRAPH_URL}/graph/edges",
            headers=headers,
            json={
                "src_node_id": src_id,
                "dst_node_id": dst_id,
                "edge_type": edge_type,
                "action": action,
                "outcome": "success",
                "risk_score": 0.3,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            edge_count += 1

    if edge_count:
        _ok(f"Seeded {edge_count} identity graph edges")

    return id_map


async def _register_agent(
    client: httpx.AsyncClient,
    user_token: str,
) -> tuple[str, str, str]:
    """Register devops-agent and return (agent_id, secret, agent_token)."""
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "X-Tenant-ID": TENANT_ID,
    }

    resp = await client.post(f"{GATEWAY}/agents", headers=headers, json={
        "name": "devops-agent-demo",
        "description": "AI DevOps governance demo — Kubernetes operations agent",
        "metadata": {"demo": True, "scenario": "devops_governance"},
    })
    data = (resp.json().get("data") or resp.json())
    agent_id = data["id"]
    _ok(f"Agent registered: {agent_id}")

    # Grant all k8s.* tools + execute_agent
    k8s_tools = [
        "k8s.get.pod", "k8s.get.deployment", "k8s.get.namespace",
        "k8s.get.node", "k8s.get.secret", "k8s.get.clusterrole",
        "k8s.list.pods", "k8s.list.deployments", "k8s.list.namespaces",
        "k8s.list.secrets", "k8s.list.clusterroles",
        "k8s.describe.deployment", "k8s.describe.pod",
        "k8s.logs.pod",
        "k8s.scale.deployment",
        "k8s.delete.namespace",  # will be hard-denied by ACP policy
        "k8s.delete.node",       # will be hard-denied by ACP policy
        "k8s.delete.pod",
        "k8s.create.clusterrolebinding",  # privilege-escalation scenario
        "k8s.exec.pod",
        "k8s.apply.configmap",
        "k8s.top.nodes",
        "execute_agent",
    ]
    for tool in k8s_tools:
        await client.post(
            f"{GATEWAY}/agents/{agent_id}/permissions",
            headers=headers,
            json={"tool_name": tool, "action": "ALLOW"},
        )
    _ok(f"Permissions granted for {len(k8s_tools)} K8s tools")

    # Issue agent credentials
    secret = f"devops-demo-{uuid.uuid4().hex[:16]}"
    cred_resp = await client.post(
        f"{IDENTITY_URL}/auth/credentials",
        headers={**headers, "X-Internal-Secret": INTERNAL_SECRET},
        json={"agent_id": agent_id, "secret": secret},
    )
    if cred_resp.status_code not in (200, 201):
        raise RuntimeError(f"Credential provisioning failed: {cred_resp.text}")
    _ok("Agent credentials provisioned")

    tok_resp = await client.post(
        f"{GATEWAY}/auth/agent/token",
        headers=headers,
        json={"agent_id": agent_id, "secret": secret},
    )
    agent_token = (tok_resp.json().get("data") or tok_resp.json())["access_token"]
    _ok("Agent JWT issued")

    return agent_id, secret, agent_token


async def _create_autonomy_contract(
    client: httpx.AsyncClient,
    headers: dict,
    agent_id: str,
) -> None:
    """Create a DevOps autonomy contract with destruction limits."""
    contract = {
        "agent_id": agent_id,
        "name": "k8s-devops-governance",
        "description": "Kubernetes DevOps agent safety contract",
        "allowed_actions": [
            "k8s.get.*", "k8s.list.*", "k8s.describe.*",
            "k8s.logs.*", "k8s.top.*",
            "k8s.scale.deployment",
            "k8s.apply.configmap",
            "k8s.delete.pod",  # allowed but requires approval
        ],
        "denied_actions": [
            "k8s.delete.namespace",
            "k8s.delete.node",
            "k8s.create.clusterrolebinding",
            "k8s.exec.*",
        ],
        "approval_required": [
            "k8s.delete.*",
            "k8s.patch.*",
        ],
        "max_cost_usd": 5.0,
        "max_runtime_seconds": 300,
    }

    resp = await client.post(
        f"{AUTONOMY_URL}/autonomy/contracts",
        headers=headers,
        json=contract,
        timeout=10,
    )
    if resp.status_code in (200, 201):
        _ok("Autonomy contract created: k8s.delete.* requires approval (fires on first destructive op)")
    else:
        print(f"  {_DIM}Autonomy contract skipped (HTTP {resp.status_code}){_RESET}")


async def main() -> None:
    print(f"\n{_BOLD}{'═'*58}{_RESET}")
    print(f"{_BOLD}  ACP DevOps Agent Governance — Demo Setup{_RESET}")
    print(f"{_BOLD}{'═'*58}{_RESET}")
    print(f"  Gateway   : {GATEWAY}")
    print(f"  Identity  : {IDENTITY_URL}")
    print(f"  Graph     : {GRAPH_URL}")
    print(f"  Autonomy  : {AUTONOMY_URL}")

    async with httpx.AsyncClient(timeout=30) as client:

        # 1. Admin authentication
        _section("Admin Authentication")
        resp = await client.post(f"{GATEWAY}/auth/token",
                                  json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                                  headers={"X-Tenant-ID": TENANT_ID})
        user_token = (resp.json().get("data") or resp.json())["access_token"]
        _ok(f"Authenticated as {ADMIN_EMAIL}")

        headers = {
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": TENANT_ID,
            "X-Internal-Secret": INTERNAL_SECRET,
        }

        # 2. Register agent
        _section("Agent Registration")
        agent_id, secret, agent_token = await _register_agent(client, user_token)

        # 3. Autonomy contract
        _section("Autonomy Contract")
        await _create_autonomy_contract(client, headers, agent_id)

        # 4. Identity graph
        await _seed_identity_graph(client, headers)

    # Persist credentials
    creds = {
        "tenant_id":    TENANT_ID,
        "agent_id":     agent_id,
        "agent_secret": secret,
        "gateway_url":  GATEWAY,
        "admin_email":  ADMIN_EMAIL,
        "admin_password": ADMIN_PASSWORD,
        "graph_url":    GRAPH_URL,
        "autonomy_url": AUTONOMY_URL,
    }
    _CREDS_FILE.write_text(json.dumps(creds, indent=2))

    print(f"\n{'═'*58}")
    print(f"{_GREEN}✅  Setup complete.{_RESET} Credentials → {_CREDS_FILE}")
    print(f"\n  agent_id  : {agent_id}")
    print(f"  tenant_id : {TENANT_ID}")
    print("\n  Run demo  : .venv/bin/python demos/devops_agent/scripted_demo.py\n")


if __name__ == "__main__":
    asyncio.run(main())
