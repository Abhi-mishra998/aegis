#!/usr/bin/env python3
"""
ACP AI Support Agent Governance — demo setup.

Provisions:
  1. A demo agent (support-agent-demo) with CRM/ticketing tool permissions
  2. An autonomy contract capping cross-tenant data access
  3. Identity graph seeded with customer/tenant topology
  4. Credentials saved to .demo_creds.json

Usage:
    .venv/bin/python demos/support_agent/setup_demo.py
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

GATEWAY       = os.getenv("ACP_GATEWAY_URL",  "http://localhost:8000")
IDENTITY_URL  = os.getenv("ACP_IDENTITY_URL", "http://localhost:8002")
GRAPH_URL     = os.getenv("ACP_GRAPH_URL",    "http://localhost:8013")
AUTONOMY_URL  = os.getenv("ACP_AUTONOMY_URL", "http://localhost:8015")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "acp_internal_prod_secret_f93284h")
TENANT_ID     = os.getenv("ACP_TENANT_ID",    "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL   = os.getenv("ACP_ADMIN_EMAIL",  "admin@acp.local")
ADMIN_PASSWORD = os.getenv("ACP_ADMIN_PASSWORD", "password")

_CREDS_FILE = Path(__file__).parent / ".demo_creds.json"

_RESET = "\033[0m"
_BOLD  = "\033[1m"
_GREEN = "\033[92m"
_DIM   = "\033[2m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {msg}")


def _section(title: str) -> None:
    print(f"\n{_BOLD}[{title}]{_RESET}")


# ── Identity graph: support platform topology ─────────────────────────────────

_SUPPORT_NODES = [
    ("agent",    "support-agent-1",    "Support Agent",
     {"role": "customer_support", "risk_level": "medium"}),

    # Tenants (customers using the SaaS platform)
    ("resource", "tenant/acme-corp",   "ACME Corp",
     {"kind": "tenant", "plan": "enterprise", "pii_allowed": True}),
    ("resource", "tenant/beta-inc",    "Beta Inc",
     {"kind": "tenant", "plan": "starter", "pii_allowed": False}),
    ("resource", "tenant/gamma-co",    "Gamma Co",
     {"kind": "tenant", "plan": "pro", "pii_allowed": True}),

    # CRM tables
    ("resource", "crm/tickets",        "tickets",
     {"kind": "crm_table", "sensitivity": "medium"}),
    ("resource", "crm/customers",      "customers",
     {"kind": "crm_table", "sensitivity": "high", "contains_pii": True}),
    ("resource", "crm/billing",        "billing",
     {"kind": "crm_table", "sensitivity": "high"}),

    # Communication channels
    ("resource", "channel/email",      "email",
     {"kind": "channel", "egress": True}),
    ("resource", "channel/slack",      "slack",
     {"kind": "channel", "egress": True}),

    # External data stores
    ("resource", "store/s3-exports",   "s3-exports",
     {"kind": "object_store", "critical": True}),
]

_SUPPORT_EDGES = [
    ("support-agent-1", "crm/tickets",   "reads",   "lookup_ticket"),
    ("support-agent-1", "crm/customers", "reads",   "get_customer_pii"),
    ("support-agent-1", "crm/billing",   "reads",   "get_billing_info"),
    ("support-agent-1", "channel/email", "invokes", "send_email"),
    ("support-agent-1", "channel/slack", "invokes", "send_slack"),
    ("support-agent-1", "store/s3-exports", "writes", "export_data"),
    ("crm/customers",   "tenant/acme-corp",  "belongs_to", "owned_by"),
    ("crm/customers",   "tenant/beta-inc",   "belongs_to", "owned_by"),
    ("crm/customers",   "tenant/gamma-co",   "belongs_to", "owned_by"),
]


async def _upsert_node(
    client: httpx.AsyncClient,
    headers: dict,
    node_type: str,
    external_id: str,
    name: str,
    attributes: dict,
) -> str | None:
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
        data = resp.json().get("data") or resp.json()
        return data.get("id")
    return None


async def _seed_identity_graph(client: httpx.AsyncClient, headers: dict) -> None:
    _section("Seeding Identity Graph")
    probe = await client.get(f"{GRAPH_URL}/graph/agents", headers=headers, timeout=5)
    if probe.status_code not in (200, 201):
        print(f"  {_DIM}Graph not reachable — skipping seed{_RESET}")
        return

    id_map: dict[str, str] = {}
    for node_type, ext_id, name, attrs in _SUPPORT_NODES:
        node_id = await _upsert_node(client, headers, node_type, ext_id, name, attrs)
        if node_id:
            id_map[ext_id] = node_id

    if id_map:
        _ok(f"Seeded {len(id_map)} graph nodes")

    edge_count = 0
    for src_ext, dst_ext, edge_type, action in _SUPPORT_EDGES:
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
                "risk_score": 0.2,
            },
            timeout=10,
        )
        if resp.status_code in (200, 201):
            edge_count += 1

    if edge_count:
        _ok(f"Seeded {edge_count} graph edges")


async def _register_agent(
    client: httpx.AsyncClient,
    user_token: str,
) -> tuple[str, str, str]:
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "X-Tenant-ID": TENANT_ID,
    }

    resp = await client.post(f"{GATEWAY}/agents", headers=headers, json={
        "name": "support-agent-demo",
        "description": "AI Support Agent Governance demo",
        "metadata": {"demo": True, "scenario": "support_governance"},
    })
    data = resp.json().get("data") or resp.json()
    agent_id = data["id"]
    _ok(f"Agent registered: {agent_id}")

    support_tools = [
        "crm.lookup_ticket",
        "crm.get_customer",
        "crm.list_customers",
        "crm.get_billing",
        "crm.update_ticket",
        "crm.bulk_export",    # will be blocked by PII density signal
        "email.send",         # hard-denied by OPA unless allowed_email_domain
        "slack.send",
        "execute_agent",
    ]
    for tool in support_tools:
        await client.post(
            f"{GATEWAY}/agents/{agent_id}/permissions",
            headers=headers,
            json={"tool_name": tool, "action": "ALLOW"},
        )
    _ok(f"Permissions granted for {len(support_tools)} support tools")

    secret = f"support-demo-{uuid.uuid4().hex[:16]}"
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
    contract = {
        "agent_id": agent_id,
        "name": "support-agent-governance",
        "description": "AI Support Agent safety contract",
        "allowed_actions": [
            "crm.lookup_ticket",
            "crm.get_customer",
            "crm.update_ticket",
            "slack.send",
        ],
        "denied_actions": [
            "crm.bulk_export",
            "crm.list_customers",
            "email.send",
        ],
        "approval_required": [
            "crm.get_billing",
        ],
        "max_tool_calls": 30,
        "max_cost_usd": 2.0,
        "max_runtime_seconds": 300,
    }
    resp = await client.post(
        f"{AUTONOMY_URL}/autonomy/contracts",
        headers=headers,
        json=contract,
        timeout=10,
    )
    if resp.status_code in (200, 201):
        _ok("Autonomy contract created: bulk_export denied, 30 ops cap")
    else:
        print(f"  {_DIM}Autonomy contract skipped (HTTP {resp.status_code}){_RESET}")


async def main() -> None:
    print(f"\n{_BOLD}{'═'*58}{_RESET}")
    print(f"{_BOLD}  ACP AI Support Agent Governance — Demo Setup{_RESET}")
    print(f"{_BOLD}{'═'*58}{_RESET}")
    print(f"  Gateway   : {GATEWAY}")
    print(f"  Identity  : {IDENTITY_URL}")
    print(f"  Graph     : {GRAPH_URL}")

    async with httpx.AsyncClient(timeout=30) as client:

        _section("Admin Authentication")
        resp = await client.post(
            f"{GATEWAY}/auth/token",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        user_token = (resp.json().get("data") or resp.json())["access_token"]
        _ok(f"Authenticated as {ADMIN_EMAIL}")

        headers = {
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": TENANT_ID,
            "X-Internal-Secret": INTERNAL_SECRET,
        }

        _section("Agent Registration")
        agent_id, secret, agent_token = await _register_agent(client, user_token)

        _section("Autonomy Contract")
        await _create_autonomy_contract(client, headers, agent_id)

        # Set tenant rpm_limit to 30 so Scenario 6 burst (35 calls) triggers 429
        _section("Tenant Rate Limit")
        rl_resp = await client.post(
            f"{GATEWAY}/auth/tenants",
            headers=headers,
            json={"tenant_id": TENANT_ID, "rpm_limit": 30},
            timeout=10,
        )
        if rl_resp.status_code in (200, 201):
            _ok("Tenant rpm_limit set to 30/min (Scenario 6 burst test)")
        else:
            print(f"  (rpm_limit update skipped — HTTP {rl_resp.status_code})")

        await _seed_identity_graph(client, headers)

    creds = {
        "tenant_id":     TENANT_ID,
        "agent_id":      agent_id,
        "agent_secret":  secret,
        "gateway_url":   GATEWAY,
        "admin_email":   ADMIN_EMAIL,
        "admin_password": ADMIN_PASSWORD,
        "graph_url":     GRAPH_URL,
        "autonomy_url":  AUTONOMY_URL,
    }
    _CREDS_FILE.write_text(json.dumps(creds, indent=2))

    print(f"\n{'═'*58}")
    print(f"{_GREEN}✅  Setup complete.{_RESET} Credentials → {_CREDS_FILE}")
    print(f"\n  agent_id  : {agent_id}")
    print(f"  tenant_id : {TENANT_ID}")
    print(f"\n  Run demo  : .venv/bin/python demos/support_agent/scripted_demo.py\n")


if __name__ == "__main__":
    asyncio.run(main())
