#!/usr/bin/env python3
"""
ACP System Reinitialization Script
====================================
Ensures production-grade state after a database wipe:
1. Seeds Admin user
2. Creates Default Tenant
3. Registers Test Agent
4. Provisions Agent Credentials
5. Grants Baseline Permissions
"""

import asyncio
import sys
import httpx

# Config
BASE_URL = "http://localhost:8000"
ADMIN_EMAIL = "admin@acp.local"
ADMIN_PASS = "password"
TENANT_ID = "00000000-0000-0000-0000-000000000001"
AGENT_ID = "11111111-1111-1111-1111-111111111111"
AGENT_SECRET = "test-agent-secret-very-long-123456"

async def reinit():
    global AGENT_ID
    print("🚀 Initializing ACP System State...")
    
    # 1. Run seed_admin.py first (direct DB access for bootstrap)
    import subprocess
    import os
    print("Step 1: Bootstrapping Admin User...")
    try:
        # Resolve path to scripts/utils/seed_admin.py
        script_dir = os.path.dirname(os.path.abspath(__file__))
        seed_script = os.path.join(script_dir, "utils", "seed_admin.py")
        
        subprocess.run([sys.executable, seed_script], check=True)
        print("  ✓ Admin user bootstrap complete")
    except Exception as e:
        print(f"  ❌ Seed Failed: {e}")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 2. Login as Admin
        print("Step 2: Authenticating Admin...")
        try:
            resp = await client.post(
                f"{BASE_URL}/auth/token",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASS}
            )
            resp.raise_for_status()
            data = resp.json()
            token_data = data.get("data", {})
            token = token_data.get("access_token") or data.get("access_token")
            headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT_ID}
            print("  ✓ Admin authenticated")
        except Exception as e:
            print(f"❌ Auth Failed: {e}")
            return

        # 3. Register Agent (in Registry)
        print("Step 3: Registering Test Agent...")
        try:
            agent_payload = {
                "id": AGENT_ID,
                "name": "production-validator-agent",
                "description": "System health validator agent",
                "owner_id": ADMIN_EMAIL
            }
            resp = await client.post(f"{BASE_URL}/agents", json=agent_payload, headers=headers)
            if resp.status_code == 201:
                AGENT_ID = resp.json().get("data", {}).get("id", AGENT_ID)
                print(f"  ✓ Agent registered: {AGENT_ID}")
            elif resp.status_code == 409:
                print("  ✓ Agent already exists")
            elif resp.status_code == 200 and resp.json().get("success", False):
                AGENT_ID = resp.json().get("data", {}).get("id", AGENT_ID)
                print(f"  ✓ Agent registered: {AGENT_ID}")
            elif resp.status_code == 200 and not resp.json().get("success", True):
                raise Exception(f"API Error: {resp.json().get('error')}")
            else:
                resp.raise_for_status()
        except Exception as e:
            if "409" not in str(e):
                print(f"  Note: Agent registration step: {e}")
            
        # If agent existed or ID didn't update, we need to fetch its true ID
        if AGENT_ID == "11111111-1111-1111-1111-111111111111":
            try:
                resp_list = await client.get(f"{BASE_URL}/agents", headers=headers)
                agents = resp_list.json().get("data", {}).get("data", [])
                for a in agents:
                    if a.get("name") == "production-validator-agent":
                        AGENT_ID = a.get("id")
                        print(f"  ✓ Found existing agent ID: {AGENT_ID}")
                        break
            except Exception as e:
                print(f"  Note: Failed to fetch existing agent: {e}")

        # 4. Provision Credentials (in Identity)
        print("Step 4: Provisioning Agent Credentials...")
        try:
            cred_payload = {
                "agent_id": AGENT_ID,
                "secret": AGENT_SECRET
            }
            resp = await client.post(f"{BASE_URL}/auth/credentials", json=cred_payload, headers=headers)
            if resp.status_code == 201:
                print("  ✓ Credentials provisioned")
            elif resp.status_code == 409:
                print("  ✓ Credentials already exist")
            elif resp.status_code == 200 and not resp.json().get("success", True):
                raise Exception(f"API Error: {resp.json().get('error')}")
            else:
                resp.raise_for_status()
        except Exception as e:
            print(f"  Note: Credential provisioning step: {e}")

        # 5. Grant Permissions (in Registry)
        print("Step 5: Granting Baseline Permissions...")
        tools = ["disk_cleanup", "log_rotate", "service_status", "metrics_collect", "read_file", "system.cleanup"]
        for tool in tools:
            try:
                perm_payload = {
                    "tool_name": tool,
                    "action": "ALLOW",
                    "granted_by": "system-init"
                }
                resp = await client.post(
                    f"{BASE_URL}/agents/{AGENT_ID}/permissions",
                    json=perm_payload,
                    headers=headers
                )
                if resp.status_code == 201:
                    print(f"  ✓ Granted: {tool}")
                elif resp.status_code == 200 and not resp.json().get("success", True):
                    print(f"  Failed to grant {tool}: API Error: {resp.json().get('error')}")
                elif resp.status_code == 409:
                    print(f"  ✓ Already granted: {tool}")
            except Exception as e:
                print(f"  Failed to grant {tool}: {e}")

    print("\n✅ ACP System Reinitialized Successfully")
    print("-" * 40)
    print(f"Tenant ID: {TENANT_ID}")
    print(f"Agent ID : {AGENT_ID}")
    print(f"Secret   : {AGENT_SECRET}")
    print("-" * 40)

if __name__ == "__main__":
    asyncio.run(reinit())
