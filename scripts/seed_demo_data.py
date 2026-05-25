#!/usr/bin/env python3
"""
ACP Demo Seed Script
====================
Populates the dashboard with realistic 30-day historical data for aegisagent.in.

Usage (from repo root with stack running):
    python scripts/seed_demo_data.py

What it does:
  1. Creates demo@aegisagent.in user (VIEWER role) via HTTP API
  2. Seeds ~2000 audit log rows (30 days) directly into acp_audit DB
  3. Seeds ~35 incidents directly into acp DB
  4. Seeds usage records directly into acp_usage DB

Idempotent: safe to re-run. Existing data is detected and skipped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Ensure repo root is on path so sdk imports work
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    import asyncpg
except ImportError:
    print("ERROR: asyncpg not installed. Run: pip install asyncpg")
    sys.exit(1)

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("ACP_BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("ACP_ADMIN_EMAIL", "admin@acp.local")
ADMIN_PASSWORD = os.getenv("ACP_ADMIN_PASSWORD", "password")

DEMO_EMAIL = "demo@aegisagent.in"
DEMO_PASSWORD = "demo"

TENANT_ID = "00000000-0000-0000-0000-000000000001"
AGENT_ID = "11111111-1111-1111-1111-111111111111"

AUDIT_CHAIN_SHARD_COUNT = 16
SEED_DAYS = 30
TARGET_AUDIT_ROWS = 2000

INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "")

# ---------------------------------------------------------------------------
# .env loader (minimal, no dotenv dependency required)
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Handles comments and quoted values."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        env[key] = val
    return env


def get_database_url() -> str:
    """Load DATABASE_URL from environment or .env file."""
    env_file = REPO_ROOT / ".env"
    file_env = load_env_file(env_file)

    url = os.getenv("DATABASE_URL") or file_env.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL not found. Set it in the environment or .env file."
        )
    return url


def derive_db_url(base_url: str, dbname: str) -> str:
    """Replace the database name in a PostgreSQL URL.

    Handles both asyncpg-style (postgresql+asyncpg://...) and plain URLs.
    Always returns a plain asyncpg URL (no +asyncpg driver prefix).
    """
    # Strip asyncpg driver prefix — asyncpg.connect() uses plain postgres:// scheme
    url = base_url.replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(url)
    # Reconstruct with new dbname
    new_path = "/" + dbname
    new = parsed._replace(path=new_path)
    plain = new.geturl()
    # asyncpg accepts "postgresql://" or "postgres://" — keep as-is
    return plain


# ---------------------------------------------------------------------------
# Realistic data fixtures
# ---------------------------------------------------------------------------

TOOLS = [
    "read_file",
    "write_file",
    "database_query",
    "network_request",
    "chat_completion",
    "export_data",
    "delete_file",
]

TOOL_WEIGHTS = [0.25, 0.18, 0.20, 0.12, 0.10, 0.08, 0.07]

ACTION_WEIGHTS = {
    "execute_tool": 0.70,
    "rate_limited": 0.15,
    "policy_denied": 0.10,
    "anomaly_detected": 0.05,
}

INCIDENT_TITLES_BY_SEVERITY = {
    "CRITICAL": [
        "SQL Injection Attempt Blocked",
        "Credential Harvesting Detected",
        "Prompt Injection Attack on LLM Agent",
        "Unauthorized Privileged Escalation Attempt",
        "Mass Data Exfiltration Blocked",
        "Ransomware Execution Pattern Detected",
        "Admin Credential Brute Force Attack",
        "Zero-Day Exploit Attempt Detected",
    ],
    "HIGH": [
        "Unusual Data Export Volume",
        "Unauthorized Network Scan Initiated",
        "Agent Accessing Restricted File Paths",
        "API Rate Limit Abuse — Potential DoS",
        "Cross-Tenant Data Access Attempt",
        "Anomalous Token Usage Spike",
        "Suspicious Outbound Connection Pattern",
        "Policy Bypass Attempt Detected",
        "Repeated Authentication Failures",
        "PII Detected in Outbound Payload",
        "Agent Spawning Unauthorized Subagents",
        "Unexpected Database Schema Query",
    ],
    "MEDIUM": [
        "Elevated Risk Score on Tool Execution",
        "Rate Limit Threshold Approached",
        "Unusual Off-Hours Agent Activity",
        "Write Operation on Read-Only Resource",
        "Agent Attempting Disabled Tool",
        "Behavior Anomaly: Session Duration Exceeded",
        "Cost Cap Warning — 80% of Monthly Budget",
        "Audit Chain Gap Detected",
        "Agent Accessing Unregistered Endpoint",
        "Deprecated API Version Usage Detected",
    ],
    "LOW": [
        "Non-Critical Policy Rule Triggered",
        "Informational: Agent Session Started",
        "Low-Risk Anomaly: Infrequent Tool Usage",
        "Configuration Drift Detected (Non-Critical)",
        "Scheduled Maintenance Window Missed",
    ],
}

INCIDENT_TRIGGERS = [
    "policy_engine",
    "behavior_firewall",
    "anomaly_detector",
    "risk_score_threshold",
    "rate_limiter",
    "audit_chain_monitor",
    "output_filter",
    "cost_cap",
]

INCIDENT_SEVERITY_COUNTS = {"CRITICAL": 8, "HIGH": 12, "MEDIUM": 10, "LOW": 5}
INCIDENT_STATUS_TARGETS = {"OPEN": 20, "INVESTIGATING": 8, "RESOLVED": 7}

# ---------------------------------------------------------------------------
# Time distribution helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(UTC)
SEED_START = NOW - timedelta(days=SEED_DAYS)


def business_hour_weight(hour: int) -> float:
    """Weight for a given hour-of-day (0-23). Bell curve around 10am-3pm."""
    # Gaussian centered at 12 (noon), sigma ~3
    return math.exp(-((hour - 12) ** 2) / (2 * 9)) + 0.05


def day_volume(dt: datetime) -> int:
    """Number of requests for the given calendar day (weekday vs weekend)."""
    if dt.weekday() < 5:  # Mon-Fri
        return random.randint(60, 120)
    else:
        return random.randint(15, 40)


def random_timestamp_for_day(day_offset: int) -> datetime:
    """Pick a realistic timestamp for the given day (0 = 30 days ago)."""
    base_day = SEED_START + timedelta(days=day_offset)
    # Pick hour weighted by business-hour curve
    hours = list(range(24))
    weights = [business_hour_weight(h) for h in hours]
    hour = random.choices(hours, weights=weights, k=1)[0]
    minute = random.randint(0, 59)
    second = random.randint(0, 59)
    return base_day.replace(
        hour=hour, minute=minute, second=second, microsecond=0, tzinfo=UTC
    )


# ---------------------------------------------------------------------------
# Audit log generation
# ---------------------------------------------------------------------------


def fake_event_hash(audit_id: uuid.UUID) -> str:
    return hashlib.sha256(audit_id.bytes).hexdigest()


def generate_audit_rows() -> list[dict]:
    """Generate ~TARGET_AUDIT_ROWS audit log rows with realistic distribution."""
    rows: list[dict] = []

    # Build a list of (day_offset, count) pairs
    schedule: list[int] = []
    for day in range(SEED_DAYS):
        count = day_volume(SEED_START + timedelta(days=day))
        schedule.extend([day] * count)

    # Trim/pad to target
    random.shuffle(schedule)
    schedule = schedule[:TARGET_AUDIT_ROWS]
    schedule.sort()  # chronological order

    # Track per-shard sequence numbers
    shard_seq: dict[int, int] = dict.fromkeys(range(AUDIT_CHAIN_SHARD_COUNT), 0)
    shard_prev: dict[int, str | None] = dict.fromkeys(range(AUDIT_CHAIN_SHARD_COUNT))

    actions = list(ACTION_WEIGHTS.keys())
    action_weights = list(ACTION_WEIGHTS.values())

    for idx, day_offset in enumerate(schedule):
        audit_id = uuid.uuid4()
        ts = random_timestamp_for_day(day_offset)

        action = random.choices(actions, weights=action_weights, k=1)[0]
        tool = random.choices(TOOLS, weights=TOOL_WEIGHTS, k=1)[0]

        # Risk score: normally distributed around 0.3, clamp [0,1]
        risk = max(0.0, min(1.0, random.gauss(0.3, 0.2)))
        # 15% of rows should be high-risk
        if random.random() < 0.15:
            risk = max(0.7, min(1.0, random.gauss(0.82, 0.1)))

        if action in ("policy_denied", "anomaly_detected"):
            decision = "deny"
            risk = max(0.6, risk)
        elif action == "rate_limited":
            decision = "deny"
        else:
            decision = "allow" if risk < 0.7 else "monitor"

        reason_map = {
            "execute_tool": None,
            "rate_limited": "rate_limit_exceeded",
            "policy_denied": random.choice(
                ["tool_not_permitted", "agent_suspended", "policy_hard_deny"]
            ),
            "anomaly_detected": random.choice(
                ["behavior_anomaly", "prompt_injection_detected", "unusual_export_volume"]
            ),
        }
        reason = reason_map[action]

        shard = idx % AUDIT_CHAIN_SHARD_COUNT
        seq = shard_seq[shard]
        prev = shard_prev[shard]
        event_hash = fake_event_hash(audit_id)

        shard_prev[shard] = event_hash
        shard_seq[shard] = seq + 1

        request_id = f"req_{audit_id.hex[:16]}"

        metadata = {
            "risk_score": round(risk, 4),
            "tool": tool,
            "agent_id": AGENT_ID,
            "source": "seed_demo_data",
            "demo": True,
        }

        rows.append(
            {
                "id": str(audit_id),
                "org_id": TENANT_ID,
                "tenant_id": TENANT_ID,
                "agent_id": AGENT_ID,
                "action": action,
                "tool": tool,
                "decision": decision,
                "reason": reason,
                "metadata_json": json.dumps(metadata),
                "request_id": request_id,
                "event_hash": event_hash,
                "prev_hash": prev,
                "chain_shard": shard,
                "billing_status": "completed",
                "timestamp": ts.isoformat(),
            }
        )

    return rows


# ---------------------------------------------------------------------------
# Incident generation
# ---------------------------------------------------------------------------


def generate_incidents() -> list[dict]:
    """Generate ~35 incidents spread across 30 days."""
    rows: list[dict] = []

    inc_num = 1000
    statuses_pool: list[str] = []
    for status, count in INCIDENT_STATUS_TARGETS.items():
        statuses_pool.extend([status] * count)
    random.shuffle(statuses_pool)

    for severity, count in INCIDENT_SEVERITY_COUNTS.items():
        titles = INCIDENT_TITLES_BY_SEVERITY[severity]
        for i in range(count):
            inc_id = str(uuid.uuid4())
            day_offset = random.randint(0, SEED_DAYS - 1)
            ts = random_timestamp_for_day(day_offset)

            status = statuses_pool.pop() if statuses_pool else "OPEN"
            title = titles[i % len(titles)]
            trigger = random.choice(INCIDENT_TRIGGERS)
            tool = random.choice(TOOLS)
            risk = round(random.uniform(0.6, 1.0) if severity == "CRITICAL" else
                         random.uniform(0.4, 0.9) if severity == "HIGH" else
                         random.uniform(0.3, 0.7) if severity == "MEDIUM" else
                         random.uniform(0.1, 0.4), 3)

            resolved_at = None
            acknowledged_at = None
            mitigated_at = None

            if status == "RESOLVED":
                acknowledged_at = (ts + timedelta(minutes=random.randint(2, 15))).isoformat()
                mitigated_at = (ts + timedelta(minutes=random.randint(15, 60))).isoformat()
                resolved_at = (ts + timedelta(hours=random.randint(1, 8))).isoformat()
            elif status == "INVESTIGATING":
                acknowledged_at = (ts + timedelta(minutes=random.randint(2, 20))).isoformat()

            timeline = [
                {
                    "ts": ts.isoformat(),
                    "event": "incident_created",
                    "actor": "system",
                }
            ]
            if acknowledged_at:
                timeline.append({"ts": acknowledged_at, "event": "acknowledged", "actor": "oncall"})
            if mitigated_at:
                timeline.append({"ts": mitigated_at, "event": "mitigated", "actor": "oncall"})
            if resolved_at:
                timeline.append({"ts": resolved_at, "event": "resolved", "actor": "oncall"})

            explanation = (
                f"Detected by {trigger} during agent execution of '{tool}'. "
                f"Risk score {risk:.2f} exceeded threshold. "
                "Automated governance response applied."
            )

            inc_num += 1
            rows.append(
                {
                    "id": inc_id,
                    "tenant_id": TENANT_ID,
                    "incident_number": f"INC-{inc_num}",
                    "agent_id": AGENT_ID,
                    "severity": severity,
                    "status": status,
                    "trigger": trigger,
                    "title": title,
                    "risk_score": risk,
                    "tool": tool,
                    "request_id": f"req_{uuid.uuid4().hex[:16]}",
                    "assigned_to": None,
                    "actions_taken": json.dumps([]),
                    "timeline": json.dumps(timeline),
                    "resolved_at": resolved_at,
                    "acknowledged_at": acknowledged_at,
                    "mitigated_at": mitigated_at,
                    "root_event_id": None,
                    "related_audit_ids": json.dumps([]),
                    "dedup_key": hashlib.sha256(f"{title}{TENANT_ID}".encode()).hexdigest()[:16],
                    "violation_count": random.randint(1, 5),
                    "explanation": explanation,
                    "created_at": ts.isoformat(),
                    "updated_at": ts.isoformat(),
                }
            )

    return rows


# ---------------------------------------------------------------------------
# Usage record generation
# ---------------------------------------------------------------------------


def generate_usage_rows(audit_rows: list[dict]) -> list[dict]:
    """Generate usage records that correspond to execute_tool audit rows."""
    rows = []
    for ar in audit_rows:
        if ar["action"] != "execute_tool":
            continue
        usage_id = str(uuid.uuid4())
        # Tokens: roughly correlated with tool type
        token_map = {
            "chat_completion": random.randint(200, 2000),
            "database_query": random.randint(50, 400),
            "export_data": random.randint(100, 800),
            "read_file": random.randint(20, 200),
            "write_file": random.randint(30, 300),
            "network_request": random.randint(10, 100),
            "delete_file": random.randint(5, 50),
        }
        tool = ar["tool"]
        units = token_map.get(tool, random.randint(50, 500))
        cost = round(units * 0.000002, 6)  # $0.000002 per token

        rows.append(
            {
                "id": usage_id,
                "tenant_id": TENANT_ID,
                "agent_id": AGENT_ID,
                "tool": tool,
                "audit_id": ar["id"],
                "units": units,
                "cost": cost,
                "timestamp": ar["timestamp"],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def connect(db_url: str) -> asyncpg.Connection:
    """Connect to PostgreSQL using asyncpg (plain postgres:// URL)."""
    # asyncpg doesn't want the postgresql:// scheme — it wants postgres://
    url = db_url.replace("postgresql://", "postgres://")
    return await asyncpg.connect(url)


async def check_audit_seeded(conn: asyncpg.Connection) -> bool:
    # Check if we already have a substantial number of rows for this tenant.
    # Using a plain COUNT avoids JSONB operator requirements for older PG versions.
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM audit_logs WHERE tenant_id = $1::uuid",
        TENANT_ID,
    )
    return (row["cnt"] if row else 0) > 100


async def check_incidents_seeded_simple(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM incidents WHERE tenant_id = $1::uuid",
        TENANT_ID,
    )
    return (row["cnt"] if row else 0) > 30


async def check_usage_seeded(conn: asyncpg.Connection) -> bool:
    row = await conn.fetchrow(
        "SELECT COUNT(*) AS cnt FROM usage_records WHERE tenant_id = $1::uuid",
        TENANT_ID,
    )
    return (row["cnt"] if row else 0) > 100


async def seed_audit_logs(conn: asyncpg.Connection, rows: list[dict]) -> None:
    """Bulk-insert audit log rows using COPY protocol (fast)."""
    print(f"  Inserting {len(rows)} audit log rows...")

    # Use executemany with ON CONFLICT DO NOTHING for safety
    sql = """
        INSERT INTO audit_logs (
            id, org_id, tenant_id, agent_id,
            action, tool, decision, reason,
            metadata_json, request_id, event_hash, prev_hash,
            chain_shard, billing_status, timestamp,
            created_at, updated_at
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4::uuid,
            $5, $6, $7, $8,
            $9::jsonb, $10, $11, $12,
            $13, $14, $15::timestamptz,
            $15::timestamptz, $15::timestamptz
        )
        ON CONFLICT DO NOTHING
    """

    batch_size = 200
    inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        records = [
            (
                r["id"],
                r["org_id"],
                r["tenant_id"],
                r["agent_id"],
                r["action"],
                r["tool"],
                r["decision"],
                r["reason"],
                r["metadata_json"],
                r["request_id"],
                r["event_hash"],
                r["prev_hash"],
                r["chain_shard"],
                r["billing_status"],
                r["timestamp"],
            )
            for r in batch
        ]
        await conn.executemany(sql, records)
        inserted += len(batch)
        print(f"    ...{inserted}/{len(rows)} rows inserted", end="\r", flush=True)

    print()


async def seed_incidents(conn: asyncpg.Connection, rows: list[dict]) -> None:
    """Bulk-insert incident rows."""
    print(f"  Inserting {len(rows)} incident rows...")

    sql = """
        INSERT INTO incidents (
            id, tenant_id, incident_number, agent_id,
            severity, status, trigger, title,
            risk_score, tool, request_id, assigned_to,
            actions_taken, timeline, resolved_at, acknowledged_at, mitigated_at,
            root_event_id, related_audit_ids, dedup_key, violation_count, explanation,
            created_at, updated_at
        ) VALUES (
            $1::uuid, $2::uuid, $3, $4,
            $5, $6, $7, $8,
            $9, $10, $11, $12,
            $13::json, $14::json,
            $15::timestamptz, $16::timestamptz, $17::timestamptz,
            $18, $19::json, $20, $21, $22,
            $23::timestamptz, $24::timestamptz
        )
        ON CONFLICT (incident_number) DO NOTHING
    """

    records = [
        (
            r["id"],
            r["tenant_id"],
            r["incident_number"],
            r["agent_id"],
            r["severity"],
            r["status"],
            r["trigger"],
            r["title"],
            r["risk_score"],
            r["tool"],
            r["request_id"],
            r["assigned_to"],
            r["actions_taken"],
            r["timeline"],
            r["resolved_at"],
            r["acknowledged_at"],
            r["mitigated_at"],
            r["root_event_id"],
            r["related_audit_ids"],
            r["dedup_key"],
            r["violation_count"],
            r["explanation"],
            r["created_at"],
            r["updated_at"],
        )
        for r in rows
    ]
    await conn.executemany(sql, records)


async def seed_usage_records(conn: asyncpg.Connection, rows: list[dict]) -> None:
    """Bulk-insert usage records."""
    print(f"  Inserting {len(rows)} usage rows...")

    sql = """
        INSERT INTO usage_records (
            id, tenant_id, agent_id, tool,
            audit_id, units, cost, timestamp
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4,
            $5::uuid, $6, $7, $8::timestamptz
        )
        ON CONFLICT DO NOTHING
    """

    records = [
        (
            r["id"],
            r["tenant_id"],
            r["agent_id"],
            r["tool"],
            r["audit_id"],
            r["units"],
            r["cost"],
            r["timestamp"],
        )
        for r in rows
    ]
    await conn.executemany(sql, records)


# ---------------------------------------------------------------------------
# Demo user creation via HTTP API
# ---------------------------------------------------------------------------


async def create_demo_user(admin_token: str) -> bool:
    """Create demo@aegisagent.in with VIEWER role via the identity API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        payload = {
            "email": DEMO_EMAIL,
            "password": DEMO_PASSWORD,
            "tenant_id": TENANT_ID,
            "org_id": TENANT_ID,
            "role": "VIEWER",
        }
        headers = {
            "Authorization": f"Bearer {admin_token}",
            "X-Tenant-ID": TENANT_ID,
        }
        resp = await client.post(f"{BASE_URL}/auth/users", json=payload, headers=headers)

        if resp.status_code == 201:
            return True
        elif resp.status_code == 400 and "already exists" in resp.text.lower():
            return False  # already exists — treat as success
        elif resp.status_code == 409:
            return False  # already exists
        else:
            raise RuntimeError(
                f"Failed to create demo user: HTTP {resp.status_code} — {resp.text[:300]}"
            )


async def get_admin_token() -> str:
    """Authenticate as admin and return JWT."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{BASE_URL}/auth/token",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"X-Tenant-ID": TENANT_ID},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Admin login failed: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        data = resp.json()
        token = (
            data.get("data", {}).get("access_token")
            or data.get("access_token")
        )
        if not token:
            raise RuntimeError(f"No access_token in login response: {data}")
        return token


async def check_demo_user_exists() -> bool:
    """Try to login as demo user to see if it already exists."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{BASE_URL}/auth/token",
            json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
            headers={"X-Tenant-ID": TENANT_ID},
        )
        return resp.status_code == 200


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    print("=" * 60)
    print("  ACP Demo Seed Script — aegisagent.in")
    print("=" * 60)
    print()

    # Resolve DB URLs
    try:
        base_db_url = get_database_url()
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    audit_db_url = derive_db_url(base_db_url, "acp_audit")
    api_db_url = derive_db_url(base_db_url, "acp")
    usage_db_url = derive_db_url(base_db_url, "acp_usage")

    print(f"Base URL : {BASE_URL}")
    print(f"Audit DB : {audit_db_url.split('@')[-1]}")
    print(f"API DB   : {api_db_url.split('@')[-1]}")
    print(f"Usage DB : {usage_db_url.split('@')[-1]}")
    print()

    # -------------------------------------------------------------------------
    # Step 1: Demo user
    # -------------------------------------------------------------------------
    print("[1/4] Demo user (demo@aegisagent.in)...")
    try:
        already_exists = await check_demo_user_exists()
        if already_exists:
            print("  SKIP — demo user already exists and can log in")
        else:
            admin_token = await get_admin_token()
            created = await create_demo_user(admin_token)
            if created:
                print(f"  OK   — Created {DEMO_EMAIL} with VIEWER role (password: {DEMO_PASSWORD})")
            else:
                print(f"  SKIP — {DEMO_EMAIL} already registered")
    except Exception as e:
        print(f"  WARN — Could not create demo user via API: {e}")
        print("         (Continuing with DB seeding...)")

    print()

    # -------------------------------------------------------------------------
    # Step 2: Audit logs
    # -------------------------------------------------------------------------
    print("[2/4] Audit logs (acp_audit database)...")
    try:
        audit_conn = await connect(audit_db_url)
        try:
            if await check_audit_seeded(audit_conn):
                print("  SKIP — audit logs already seeded")
            else:
                random.seed(42)  # reproducible
                audit_rows = generate_audit_rows()
                await seed_audit_logs(audit_conn, audit_rows)
                print(f"  OK   — {len(audit_rows)} audit log rows seeded ({SEED_DAYS} days)")
        finally:
            await audit_conn.close()
    except Exception as e:
        print(f"  ERROR — Audit log seeding failed: {e}")
        import traceback
        traceback.print_exc()
        audit_rows = []

    print()

    # -------------------------------------------------------------------------
    # Step 3: Incidents
    # -------------------------------------------------------------------------
    print("[3/4] Incidents (acp database)...")
    try:
        api_conn = await connect(api_db_url)
        try:
            if await check_incidents_seeded_simple(api_conn):
                print("  SKIP — incidents already seeded (>30 rows found)")
            else:
                random.seed(43)  # reproducible
                incident_rows = generate_incidents()
                await seed_incidents(api_conn, incident_rows)
                sev_summary = ", ".join(
                    f"{k}: {v}" for k, v in INCIDENT_SEVERITY_COUNTS.items()
                )
                print(f"  OK   — {len(incident_rows)} incidents seeded ({sev_summary})")
        finally:
            await api_conn.close()
    except Exception as e:
        print(f"  ERROR — Incident seeding failed: {e}")
        import traceback
        traceback.print_exc()

    print()

    # -------------------------------------------------------------------------
    # Step 4: Usage records
    # -------------------------------------------------------------------------
    print("[4/4] Usage records (acp_usage database)...")
    try:
        usage_conn = await connect(usage_db_url)
        try:
            if await check_usage_seeded(usage_conn):
                print("  SKIP — usage records already seeded (>100 rows found)")
            else:
                # Re-generate audit rows deterministically to get same UUIDs
                random.seed(42)
                audit_rows_for_usage = generate_audit_rows()
                random.seed(44)
                usage_rows = generate_usage_rows(audit_rows_for_usage)
                await seed_usage_records(usage_conn, usage_rows)
                total_cost = sum(r["cost"] for r in usage_rows)
                total_tokens = sum(r["units"] for r in usage_rows)
                print(
                    f"  OK   — {len(usage_rows)} usage records seeded "
                    f"({total_tokens:,} tokens, ${total_cost:.2f} total cost)"
                )
        finally:
            await usage_conn.close()
    except Exception as e:
        print(f"  ERROR — Usage seeding failed: {e}")
        import traceback
        traceback.print_exc()

    print()
    print("=" * 60)
    print("  Seeding complete!")
    print()
    print("  Demo credentials:")
    print(f"    Email    : {DEMO_EMAIL}")
    print(f"    Password : {DEMO_PASSWORD}")
    print(f"    Tenant   : {TENANT_ID}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
