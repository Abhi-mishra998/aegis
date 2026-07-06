# Laptop fallback — run the full Aegis demo locally in under 2 minutes

> The point: if AWS is misbehaving and `https://ha.aegisagent.in/live-demo`
> is degraded, you can put the prospect on the same demo running on the
> founder's laptop. This file is the recipe.

## Prereqs (one-time, ~3 minutes)

```bash
# Docker Desktop running (Mac/Linux/WSL2)
docker info >/dev/null && echo "docker OK"

# clone the repo to wherever you keep code
git clone <repo-url> aegis && cd aegis

# pull the Groq key from AWS Secrets Manager (the same one prod uses)
export GROQ_API_KEY=$(aws secretsmanager get-secret-value \
  --region ap-south-1 \
  --secret-id acp-prodha/groq_api_key \
  --query SecretString --output text)
echo "GROQ_API_KEY length: ${#GROQ_API_KEY} (must be 56, must start with gsk_)"

# generate a minimal .env from the template
cp infra/.env.example infra/.env 2>/dev/null || true
echo "GROQ_API_KEY=${GROQ_API_KEY}" >> infra/.env

# anything that requires JWT_SECRET_KEY etc. just needs a value — local
# tenants are created on first boot, none of these need to match prod.
grep -q '^JWT_SECRET_KEY=' infra/.env || echo "JWT_SECRET_KEY=local-dev-secret-CHANGE-ME-CHANGE-ME-CHANGE-ME-CHANGE-ME" >> infra/.env
grep -q '^INTERNAL_SECRET=' infra/.env || echo "INTERNAL_SECRET=local-dev-internal-secret-CHANGE-ME" >> infra/.env
grep -q '^MESH_JWT_SECRET=' infra/.env || echo "MESH_JWT_SECRET=local-dev-mesh-secret-CHANGE-ME" >> infra/.env
grep -q '^GRAFANA_ADMIN_PASSWORD=' infra/.env || {
  read -s -p "Grafana admin password (local only, min 12 chars): " _p; echo
  echo "GRAFANA_ADMIN_PASSWORD=$_p" >> infra/.env; unset _p
}
```

## Boot the stack (~90 seconds)

```bash
cd infra
docker compose up -d --build
# wait until everything is healthy (gateway has a 120 s start_period)
docker compose ps
```

When `docker compose ps` shows all containers `healthy`, you're ready.

## Run the demo

Open `http://localhost:5173/live-demo` (the UI's port is 5173 locally,
NOT 80 — Vite's choice for dev consistency).

Log in with the seed admin. Credentials come from what you set when running
`scripts/utils/seed_admin.py` — the script prompts for them and never
writes anything to disk:

```
Tenant ID:  00000000-0000-0000-0000-000000000001
Email:      $AEGIS_ADMIN_EMAIL
Password:   $AEGIS_ADMIN_PASSWORD
```

Type the prompt. Click run. Same trace as prod.

## Or hit `/demo/groq-agent` directly via curl

```bash
TOKEN=$(curl -s http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -d "{\"email\":\"$AEGIS_ADMIN_EMAIL\",\"password\":\"$AEGIS_ADMIN_PASSWORD\"}" | jq -r .data.access_token)

curl -s http://localhost:8000/demo/groq-agent \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: 00000000-0000-0000-0000-000000000001" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Clean up old logs in /var/log."}' | jq .data.summary
```

Expect: `{"allow": 2, "deny": 1, ...}` or similar — `rm -rf` denied.

## Tear down

```bash
cd infra
docker compose down -v
```

## When the demo is degraded *and* docker isn't running either

Open `http://localhost:5173/demo-fallback.html` (or
`https://ha.aegisagent.in/demo-fallback.html` — served by the UI's nginx
even when the gateway is dead).  Static replay of the three canonical
prompts, every BLOCKED verdict really came from the production policy
engine.
