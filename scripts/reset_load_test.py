
import asyncio
import subprocess

from redis.asyncio import Redis


async def reset() -> None:
    print("Resetting ACP Data for Clean Load Test...")

    # 1. Clear Databases
    for db in ["acp_audit", "acp_usage", "acp_identity"]:
        subprocess.run(["docker", "exec", "acp_postgres", "psql", "-U", "postgres", "-d", db, "-c", "TRUNCATE TABLE audit_logs, usage_records, users, agents CASCADE;"], capture_output=True)

    # 2. Clear Redis
    r = Redis.from_url("redis://localhost:6379/0")
    await r.flushall()
    await r.aclose()

    # 3. Re-init Admin User
    subprocess.run(["python3", "scripts/reinit_system.py"])

    print("ACP System Reset Complete.")

if __name__ == "__main__":
    asyncio.run(reset())
