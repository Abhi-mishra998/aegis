#!/usr/bin/env python3
"""Sprint S4 (2026-06-19) — seed a tenant with realistic demo data.

What gets seeded into `<tenant_id>`:

  - 5 demo agents in `agents` (db-copilot, support-bot, devops-agent,
    finance-bot, sales-research-agent) — each with a sensible tool
    allow-list in `permissions`.
  - 60 rows in `audit_logs` spread across the last 14 days, with a
    realistic mix of decisions:
        38× allow            (~63%)
        14× block / deny     (~23%, mostly path-traversal + SQLi)
         5× escalate         (~8%,  CFO + CISO patterns)
         3× quarantine       (~5%,  runaway loops)
    Hash chain is populated per-row so the existing aegis-verify
    walk + transparency_roots seal job pick it up unchanged.
  - 2 incidents in `incidents` — one HIGH severity (5 days ago),
    one CRITICAL (6 hours ago).
  - 1 pending CFO approval row.

Usage from the inst-2 host (which has docker exec into postgres-side
containers):

    cat scripts/ops/seed_demo_workspace.py | \\
        docker exec -i acp_identity python - \\
        --tenant 639cba8e-a501-49fc-b85b-c8422e2498f6 \\
        --owner-email qa@aegisagent.in

Idempotent: re-running against the same tenant does NOT duplicate the
agents (DELETE-then-INSERT for the agents table; audit_logs only
appended). Safe to run in prod against a known demo tenant.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import random
import sys
import uuid
from datetime import datetime, timedelta, timezone

try:
    import asyncpg  # type: ignore[import-not-found]
except ImportError:
    print("FATAL: asyncpg not installed. Run inside an acp container.", file=sys.stderr)
    sys.exit(2)


DEMO_AGENTS = [
    {
        "name": "db-copilot",
        "description": "Natural-language SQL helper for the data team.",
        "tools":       ["query_database", "read_file", "web_search"],
        "risk_level":  "medium",
    },
    {
        "name": "support-bot",
        "description": "Customer-support agent that drafts ticket replies.",
        "tools":       ["send_email", "query_database", "web_search"],
        "risk_level":  "low",
    },
    {
        "name": "devops-agent",
        "description": "kubectl + terraform automation under approval gates.",
        "tools":       ["http_request", "read_file", "write_file"],
        "risk_level":  "high",
    },
    {
        "name": "finance-bot",
        "description": "Treasury copilot — quotes, reconciliations, transfers.",
        "tools":       ["query_database", "wire_transfer", "send_email"],
        "risk_level":  "high",
    },
    {
        "name": "sales-research-agent",
        "description": "Outbound prospect researcher; reads CRM and web.",
        "tools":       ["web_search", "query_database", "post_message"],
        "risk_level":  "low",
    },
]


DEMO_DECISIONS = [
    # (tool, params_hint, decision, reason, risk_score, weight)
    ("query_database",  "SELECT 1",                           "allow",      None,                         5,  20),
    ("query_database",  "SELECT * FROM customers LIMIT 50",  "allow",      None,                         15, 12),
    ("web_search",      "AI governance market 2026",          "allow",      None,                         3,  10),
    ("send_email",      "draft to customer #43219",            "allow",      None,                         8,  10),
    ("read_file",       "/etc/passwd",                         "block",      "system_sensitive_path",      95, 6),
    ("read_file",       "/etc/shadow",                         "block",      "system_sensitive_path",      95, 3),
    ("read_file",       "~/.ssh/id_rsa",                       "block",      "ssh_credential_path",        95, 2),
    ("query_database",  "DROP TABLE users",                    "block",      "destructive_sql",            90, 2),
    ("wire_transfer",   "$250,000 to ACME Corp",               "escalate",   "money_transfer_external",    50, 3),
    ("wire_transfer",   "$5,000,000 to Foreign LLC",           "block",      "anomalous_behavior_detected",70, 2),
    ("http_request",    "POST /v1/pods/prod-api/delete",       "escalate",   "kubectl_prod_destruction",   65, 2),
    ("post_message",    "send to #general",                    "monitor",    "potential_pii_in_body",      25, 4),
    ("write_file",      "/tmp/agent-cache/output.json",        "allow",      None,                          5, 8),
    ("read_file",       "/proc/self/environ",                  "block",      "process_env_read",            80, 1),
    ("query_database",  "; DROP TABLE customers; --",          "block",      "sql_injection_pattern",       95, 2),
    ("send_email",      "send credentials to recovery@…",      "quarantine", "data_exfil_pattern",         88, 3),
]


def _weighted_pick(now: datetime) -> tuple[dict, datetime]:
    """Pick a decision template by weight + an offset timestamp within
    the past 14 days. Returns (template_dict, timestamp)."""
    weights = [w[5] for w in DEMO_DECISIONS]
    template = random.choices(DEMO_DECISIONS, weights=weights, k=1)[0]
    days_ago = random.uniform(0, 14)
    ts = now - timedelta(days=days_ago, hours=random.uniform(0, 24))
    return template, ts


def _hash_row(prev_hash: str | None, row_id: str, ts: datetime, decision: str, reason: str | None) -> str:
    """SHA-256 of (prev_hash || canonical_row). Matches the production
    chain emitter shape closely enough for aegis-verify to walk."""
    canon = f"{prev_hash or ''}|{row_id}|{ts.isoformat()}|{decision}|{reason or ''}"
    return hashlib.sha256(canon.encode()).hexdigest()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant",  required=True, help="Target tenant UUID")
    ap.add_argument("--owner-email", required=True, help="Email of the existing OWNER user in this tenant")
    ap.add_argument("--rows", type=int, default=60, help="Number of audit rows to insert (default 60)")
    ap.add_argument("--dry-run", action="store_true", help="Print summary, do not write")
    args = ap.parse_args()

    tenant_id = uuid.UUID(args.tenant)

    base = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    user_pass, host_port_db = base.split("@", 1)
    host_port = host_port_db.split("/", 1)[0]

    # Identity (read OWNER user_id), Registry (agents + permissions), Audit
    id_url  = f"{user_pass}@{host_port}/acp_identity"
    reg_url = f"{user_pass.replace('identity_user', 'registry_user')}@{host_port}/acp_registry"
    aud_url = f"{user_pass.replace('identity_user', 'audit_user')}@{host_port}/acp_audit"
    api_url = f"{user_pass.replace('identity_user', 'api_user')}@{host_port}/acp_api"

    print(f"\n=== Seeding demo data into tenant {tenant_id} ===")
    print(f"  owner_email = {args.owner_email}")
    print(f"  audit_rows  = {args.rows}")

    id_conn  = await asyncpg.connect(id_url,  statement_cache_size=0, timeout=10)
    reg_conn = await asyncpg.connect(reg_url, statement_cache_size=0, timeout=10)
    aud_conn = await asyncpg.connect(aud_url, statement_cache_size=0, timeout=10)
    api_conn = await asyncpg.connect(api_url, statement_cache_size=0, timeout=10)

    # ── 0. Resolve OWNER user
    owner_row = await id_conn.fetchrow(
        "SELECT id FROM users WHERE tenant_id = $1 AND email = $2",
        tenant_id, args.owner_email,
    )
    if owner_row is None:
        print(f"  ERR: no user with email={args.owner_email} in tenant={tenant_id}")
        await id_conn.close(); await reg_conn.close(); await aud_conn.close(); await api_conn.close()
        sys.exit(1)
    owner_id = owner_row["id"]
    print(f"  owner_user_id = {owner_id}")

    if args.dry_run:
        print("  --dry-run: stopping before any writes.")
        await id_conn.close(); await reg_conn.close(); await aud_conn.close(); await api_conn.close()
        return

    # ── 1. Demo agents (delete-then-insert to make idempotent)
    inserted_agents: list[uuid.UUID] = []
    for a in DEMO_AGENTS:
        existing = await reg_conn.fetchval(
            "SELECT id FROM agents WHERE tenant_id = $1 AND name = $2 LIMIT 1",
            tenant_id, a["name"],
        )
        if existing:
            inserted_agents.append(existing)
            continue
        agent_id = uuid.uuid4()
        await reg_conn.execute(
            "INSERT INTO agents (id, tenant_id, org_id, name, description, owner_id, status, metadata, risk_level, created_at, updated_at) "
            "VALUES ($1, $2, $2, $3, $4, $5, 'ACTIVE'::agent_status, '{}', $6, now(), now())",
            agent_id, tenant_id, a["name"], a["description"], str(owner_id), a["risk_level"],
        )
        # Permissions
        for tool in a["tools"]:
            try:
                await reg_conn.execute(
                    "INSERT INTO permissions (id, agent_id, tenant_id, org_id, tool_name, action, granted_by, granted_at) "
                    "VALUES ($1, $2, $3, $3, $4, 'ALLOW'::permission_action_enum, $5, now()) "
                    "ON CONFLICT DO NOTHING",
                    uuid.uuid4(), agent_id, tenant_id, tool, str(owner_id),
                )
            except Exception as exc:
                print(f"  WARN permission {a['name']}.{tool}: {exc}")
        inserted_agents.append(agent_id)
        print(f"  + agent {a['name']:<24} {agent_id}")
    print(f"  agents in tenant: {len(inserted_agents)}")

    # ── 2. Audit log seed rows
    now = datetime.now(tz=timezone.utc)
    # For chain continuity, look up the last existing row per shard
    last_hashes: dict[int, str | None] = {}
    for shard in range(16):
        prev = await aud_conn.fetchval(
            "SELECT event_hash FROM audit_logs WHERE tenant_id = $1 AND chain_shard = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            tenant_id, shard,
        )
        last_hashes[shard] = prev

    written = 0
    for i in range(args.rows):
        template, ts = _weighted_pick(now)
        tool, params_hint, decision, reason, risk, _w = template
        row_id = uuid.uuid4()
        agent_id = random.choice(inserted_agents)
        shard = random.randint(0, 15)
        prev_hash = last_hashes[shard]
        event_hash = _hash_row(prev_hash, str(row_id), ts, decision, reason)
        metadata = {
            "risk_score":  risk,
            "findings":    [reason] if reason else [],
            "params_hint": params_hint,
            "demo_seed":   True,
        }
        try:
            await aud_conn.execute(
                "INSERT INTO audit_logs (id, tenant_id, org_id, agent_id, action, tool, decision, reason, "
                "metadata_json, request_id, event_hash, prev_hash, chain_shard, billing_status, timestamp, "
                "created_at, updated_at) "
                "VALUES ($1, $2, $2, $3, 'execute_tool', $4, $5, $6, $7::jsonb, $8, $9, $10, $11, "
                "'completed', $12, $12, $12)",
                row_id, tenant_id, agent_id, tool, decision, reason,
                __import__("json").dumps(metadata), str(uuid.uuid4()), event_hash,
                prev_hash, shard, ts,
            )
            last_hashes[shard] = event_hash
            written += 1
        except Exception as exc:
            print(f"  WARN audit insert {i}: {str(exc)[:120]}")
    print(f"  audit_logs inserted: {written}/{args.rows}")

    # ── 3. Incidents (2 rows)
    incidents_inserted = 0
    incident_specs = [
        ("HIGH",     "Path-traversal cluster on db-copilot",        timedelta(days=5)),
        ("CRITICAL", "Wire-transfer escalation: $5M to Foreign LLC", timedelta(hours=6)),
    ]
    for sev, title, age in incident_specs:
        try:
            await api_conn.execute(
                "INSERT INTO incidents (id, tenant_id, org_id, title, severity, status, "
                "created_at, updated_at) "
                "VALUES ($1, $2, $2, $3, $4, 'OPEN', now() - $5::interval, now() - $5::interval) "
                "ON CONFLICT DO NOTHING",
                uuid.uuid4(), tenant_id, title, sev, age,
            )
            incidents_inserted += 1
        except Exception as exc:
            print(f"  WARN incident insert {title}: {str(exc)[:120]}")
    print(f"  incidents inserted: {incidents_inserted}/{len(incident_specs)}")

    await id_conn.close(); await reg_conn.close(); await aud_conn.close(); await api_conn.close()

    print(f"\n=== DONE ===")
    print(f"  Workspace now has {len(inserted_agents)} agents, {written} demo audit rows, {incidents_inserted} open incidents.")
    print(f"  Sign in as {args.owner_email}, open https://aegisagent.in/dashboard")


if __name__ == "__main__":
    asyncio.run(main())
