import asyncio, httpx
from sdk.common.auth import mint_service_token

TENANT = "462d6e58-559f-44f3-8b0f-185aa9235909"

async def go():
    tok = mint_service_token("gateway")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post("http://api:8000/api-keys/employees",
            headers={"X-Mesh-Token": tok, "X-Tenant-ID": TENANT, "Content-Type": "application/json"},
            json={"email": "chart-test@aegis-test.example.com", "name": "chart-test"})
        d = r.json().get("data", {})
        api_key = d.get("api_key", "")
        print(f"KEY={api_key}")

        r2 = await c.post("http://registry:8000/agents",
            headers={"X-Mesh-Token": tok, "X-Tenant-ID": TENANT, "Content-Type": "application/json", "X-Actor": "chart"},
            json={"name": "chart-agent", "description": "chart test", "owner_id": "chart", "risk_level": "low"})
        aid = r2.json().get("data", {}).get("id", "")
        print(f"AID={aid}")

        r3 = await c.post(f"http://registry:8000/agents/{aid}/permissions",
            headers={"X-Mesh-Token": tok, "X-Tenant-ID": TENANT, "Content-Type": "application/json"},
            json={"tool_name": "search_web", "action": "ALLOW", "granted_by": "chart"})
        print(f"perm={r3.status_code}")

asyncio.run(go())
