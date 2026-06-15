#!/usr/bin/env python3
"""
DB Copilot demo setup: creates demo schema + seed data in existing Postgres,
registers the demo agent, and prints the agent credentials needed by scripted_demo.py.

Usage:
    .venv/bin/python demos/db_copilot/setup_demo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import string
import sys
import uuid
from pathlib import Path

import httpx

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))

# Load .env from project root so scripts work on any environment without
# requiring the caller to export variables manually.
_env_file = _ROOT / ".env"
if _env_file.exists():
    with open(_env_file) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

GATEWAY = os.getenv("ACP_GATEWAY_URL", "https://ha.aegisagent.in")
IDENTITY_URL = os.getenv("ACP_IDENTITY_URL", "https://ha.aegisagent.in")
PG_DSN = os.getenv(
    "ACP_PG_DSN",
    "postgresql://postgres:postgres@localhost:5433/acp_identity",
)
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET") or ""
if not INTERNAL_SECRET:
    raise SystemExit("ERROR: INTERNAL_SECRET not set. Add it to .env or export it.")
TENANT_ID = os.getenv("ACP_TENANT_ID", "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL = os.getenv("ACP_ADMIN_EMAIL", "admin@acp.local")
ADMIN_PASSWORD = os.getenv("ACP_ADMIN_PASSWORD", "password")

_DEMO_DSN = os.getenv(
    "DEMO_PG_DSN",
    "postgresql+asyncpg://postgres:postgres@localhost:5433/acp_demo",
)

_FIRST_NAMES = [
    "Alice", "Bob", "Carlos", "Diana", "Eve", "Frank", "Grace", "Hiro",
    "Iris", "James", "Keiko", "Liam", "Maya", "Nadia", "Omar", "Priya",
    "Quinn", "Rosa", "Sam", "Tara", "Uma", "Viktor", "Wendy", "Xin",
    "Yara", "Zoe",
]
_LAST_NAMES = [
    "Smith", "Jones", "Chen", "Patel", "Kim", "Garcia", "Martinez",
    "Lee", "Brown", "Wilson", "Taylor", "Anderson", "Thomas", "Jackson",
    "White", "Harris", "Martin", "Thompson", "Moore", "Walker",
]
_DOMAINS = ["example.com", "corp.io", "enterprise.net", "biz.co", "demo.org"]


def _rand_ssn() -> str:
    return f"{random.randint(100,999):03d}-{random.randint(10,99):02d}-{random.randint(1000,9999):04d}"


def _rand_cc() -> str:
    return f"4{random.randint(100,999):03d}-{random.randint(1000,9999):04d}-{random.randint(1000,9999):04d}-{random.randint(1000,9999):04d}"


def _rand_salary() -> int:
    return random.randint(45_000, 250_000)


async def _create_demo_db() -> None:
    """Create demo_copilot schema with customers + orders in acp_demo database."""
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError:
        print("ERROR: sqlalchemy not available — pip install sqlalchemy[asyncio] asyncpg")
        return

    # Connect to postgres default DB to create acp_demo if it doesn't exist.
    # Derive the admin DSN from the same host/port as DEMO_PG_DSN so this works
    # against any deployment (was previously hardcoded to localhost:5433).
    admin_dsn = os.getenv(
        "DEMO_ADMIN_PG_DSN",
        _DEMO_DSN.rsplit("/", 1)[0] + "/postgres",
    )
    engine_admin = create_async_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    async with engine_admin.connect() as conn:
        result = await conn.execute(text("SELECT 1 FROM pg_database WHERE datname='acp_demo'"))
        if not result.fetchone():
            await conn.execute(text("CREATE DATABASE acp_demo"))
            print("✓ Created database acp_demo")
        else:
            print("✓ Database acp_demo already exists")
    await engine_admin.dispose()

    engine = create_async_engine(_DEMO_DSN, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS demo_copilot"))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS demo_copilot.customers (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                ssn         TEXT,
                credit_card TEXT,
                salary      INTEGER,
                region      TEXT DEFAULT 'US',
                created_at  TIMESTAMPTZ DEFAULT now()
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS demo_copilot.orders (
                id          SERIAL PRIMARY KEY,
                customer_id INTEGER REFERENCES demo_copilot.customers(id),
                product     TEXT NOT NULL,
                amount      NUMERIC(10,2) NOT NULL,
                status      TEXT DEFAULT 'pending',
                created_at  TIMESTAMPTZ DEFAULT now()
            )
        """))

        count = (await conn.execute(text("SELECT COUNT(*) FROM demo_copilot.customers"))).scalar()
        if count and count >= 100:
            print(f"✓ Demo tables exist ({count} customers), skipping seed")
        else:
            rows = []
            for _i in range(500):
                fn = random.choice(_FIRST_NAMES)
                ln = random.choice(_LAST_NAMES)
                domain = random.choice(_DOMAINS)
                suffix = "".join(random.choices(string.digits, k=4))
                rows.append({
                    "name": f"{fn} {ln}",
                    "email": f"{fn.lower()}.{ln.lower()}{suffix}@{domain}",
                    "ssn": _rand_ssn(),
                    "credit_card": _rand_cc(),
                    "salary": _rand_salary(),
                    "region": random.choice(["US", "EU", "APAC", "LATAM"]),
                })
            for row in rows:
                await conn.execute(text("""
                    INSERT INTO demo_copilot.customers (name, email, ssn, credit_card, salary, region)
                    VALUES (:name, :email, :ssn, :credit_card, :salary, :region)
                    ON CONFLICT (email) DO NOTHING
                """), row)

            products = ["Pro Plan", "Enterprise", "Starter", "Add-on: Compliance", "Add-on: AI"]
            cust_ids = [r[0] for r in (await conn.execute(text("SELECT id FROM demo_copilot.customers LIMIT 500"))).fetchall()]
            for cid in cust_ids[:300]:
                for _ in range(random.randint(1, 3)):
                    await conn.execute(text("""
                        INSERT INTO demo_copilot.orders (customer_id, product, amount, status)
                        VALUES (:cid, :product, :amount, :status)
                    """), {
                        "cid": cid,
                        "product": random.choice(products),
                        "amount": round(random.uniform(9.99, 999.99), 2),
                        "status": random.choice(["completed", "pending", "refunded"]),
                    })
            print("✓ Seeded 500 customers + ~750 orders")

    await engine.dispose()


async def _register_demo_agent(client: httpx.AsyncClient, user_token: str) -> tuple[str, str, str]:
    """Register db-copilot agent and return (agent_id, secret, agent_token)."""
    headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "X-Tenant-ID": TENANT_ID,
    }

    # Create agent
    resp = await client.post(f"{GATEWAY}/agents", headers=headers, json={
        "name": "db-copilot-demo",
        "description": "AI database copilot — SQL governance demo agent",
        "metadata": {"demo": True},
    })
    data = resp.json().get("data") or resp.json()
    agent_id = data["id"]
    print(f"✓ Agent registered: {agent_id}")

    # Grant permissions
    for tool in ("db.query", "db.execute", "execute_agent"):
        await client.post(f"{GATEWAY}/agents/{agent_id}/permissions", headers=headers,
                          json={"tool_name": tool, "action": "ALLOW"})
    print("✓ Permissions granted: db.query, db.execute, execute_agent")

    # Provision credentials
    secret = f"demo-secret-{uuid.uuid4().hex[:16]}"
    cred_resp = await client.post(
        f"{IDENTITY_URL}/auth/credentials",
        headers={**headers, "X-Internal-Secret": INTERNAL_SECRET},
        json={"agent_id": agent_id, "secret": secret},
    )
    if cred_resp.status_code not in (200, 201):
        raise RuntimeError(f"Credential provisioning failed: {cred_resp.text}")
    print("✓ Agent credentials provisioned")

    # Get agent token
    tok_resp = await client.post(f"{GATEWAY}/auth/agent/token", headers=headers,
                                 json={"agent_id": agent_id, "secret": secret})
    agent_token = tok_resp.json()["data"]["access_token"]
    print("✓ Agent JWT issued")

    return agent_id, secret, agent_token


async def main() -> None:
    print("── ACP DB Copilot Demo Setup ──")

    # Step 1: Create demo DB
    print("\n[1/3] Creating demo database schema + seed data…")
    await _create_demo_db()

    # Step 2: Get admin token
    print("\n[2/3] Authenticating as admin…")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{GATEWAY}/auth/token", json={
            "email": ADMIN_EMAIL, "password": ADMIN_PASSWORD,
        }, headers={"X-Tenant-ID": TENANT_ID})
        user_token = resp.json()["data"]["access_token"]
        print("✓ Admin authenticated")

        # Step 3: Register agent
        print("\n[3/3] Registering demo agent…")
        agent_id, secret, agent_token = await _register_demo_agent(client, user_token)

    # Persist credentials for scripted_demo.py
    creds = {
        "tenant_id": TENANT_ID,
        "agent_id": agent_id,
        "agent_secret": secret,
        "gateway_url": GATEWAY,
        "admin_email": ADMIN_EMAIL,
        "admin_password": ADMIN_PASSWORD,
    }
    creds_path = Path(__file__).parent / ".demo_creds.json"
    creds_path.write_text(json.dumps(creds, indent=2))

    print(f"\n✅  Setup complete. Credentials saved to {creds_path}")
    print(f"\n    agent_id  : {agent_id}")
    print(f"    secret    : {secret}")
    print(f"    token[:40]: {agent_token[:40]}…")
    print("\n    Run the demo: .venv/bin/python demos/db_copilot/scripted_demo.py")


if __name__ == "__main__":
    asyncio.run(main())
