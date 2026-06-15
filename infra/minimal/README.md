# Aegis self-host — minimal mode

> Three containers. One externally-reachable port. One file you can read
> from top to bottom to review the deployment.

This directory packages Aegis for a customer who wants to run it inside
their own VPC — not as a SaaS tenant. The goal is that a small security
team can:

1. Read every file in this directory in under an hour.
2. Boot the stack on one Linux host in under five minutes.
3. Confirm that the runtime block + tamper-evident audit behaviour is
   identical to the prod-ha demo running at `ha.aegisagent.in`.

If that's not the install shape you want, see `infra/docker-compose.yml`
(the 22-container prod-ha topology) or `infra/helm/` (the Kubernetes
chart). This page documents the **3-container** shape.

---

## What you see vs what's inside

The deployment surface is intentionally narrow:

```
                              ┌─────────────────────────┐
   POST /execute  ────────▶   │       aegis-core        │   ◀──── one public port
   GET  /receipts/key         │   (single container)    │         (8000)
   GET  /compliance/export    │                         │
                              │  supervisord            │
                              │   ├─ OPA               (127.0.0.1:8181)
                              │   ├─ identity          (127.0.0.1:8002)
                              │   ├─ registry          (127.0.0.1:8001)
                              │   ├─ policy            (127.0.0.1:8003)
                              │   ├─ behavior          (127.0.0.1:8007)
                              │   ├─ decision          (127.0.0.1:8010)
                              │   ├─ audit             (127.0.0.1:8004)
                              │   ├─ autonomy          (127.0.0.1:8015)
                              │   └─ gateway           ( 0.0.0.0:8000)
                              └────────────┬────────────┘
                                           │ private compose network
                              ┌────────────┴────────────┐
                              ▼                         ▼
                   ┌────────────────────┐    ┌────────────────────┐
                   │  aegis-postgres    │    │   aegis-redis      │
                   │  postgres:15       │    │   redis:7          │
                   │  no external port  │    │   no external port │
                   └────────────────────┘    └────────────────────┘
```

**What a CISO inspecting the host sees:**

- 3 docker services (`aegis-core`, `aegis-postgres`, `aegis-redis`)
- 1 externally-reachable TCP port (`:8000`)
- 1 environment file at `infra/minimal/.env`
- 1 entrypoint script at `/usr/local/bin/aegis-start` (sourced from
  `infra/minimal/start.sh` — readable in 60 seconds)

**What's actually running inside `aegis-core`:**

Ten supervised processes, identical to the prod-ha images. Each binds
`127.0.0.1`, so the only way into them is via the gateway on
`0.0.0.0:8000`. We don't hide the multi-process structure — that would
be theatre. We just don't make it the surface area you have to defend.

The reason for ten processes inside one container instead of ten
containers is **review cost, not technical purity**: a 22-container
deployment forces 22 sets of network policies, service accounts, image
versions, and observation surfaces. A self-host customer doesn't need
that yet. They need "block, log, prove, kill switch" — which is what
this image runs.

---

## Files in this directory

| File | What it is | Read it for |
|---|---|---|
| `Dockerfile` | Multi-stage build on `python:3.11-slim`. Bundles OPA 1.17.1 binary, supervisord, postgresql-client, curl, and the Aegis Python stack. | The full software bill of materials of `aegis/core:minimal`. |
| `supervisord.conf` | Process supervisor config — declares the 10 inner programs, their ports, their env vars, their restart policy. | The exact set of processes that run inside `aegis-core`. |
| `start.sh` | Entrypoint. Waits for Postgres + Redis, bootstraps the 4 per-service DBs + users, runs alembic migrations, then `exec`s supervisord. | What gets touched on the first boot. Idempotent on re-runs. |
| `docker-compose.minimal.yml` | The 3-service compose file. | The shape of the deployment from the host's point of view. |
| `.env.example` | Required + optional environment variables. Copy to `.env`, do NOT commit `.env`. | The full list of secrets the install needs. |
| `README.md` | This file. | What you're reading. |

---

## Quick start

```bash
# 1. Clone + cd
git clone https://github.com/Abhi-mishra998/aegis.git
cd aegis/infra/minimal

# 2. Generate two long secrets
echo "INTERNAL_SECRET=$(openssl rand -hex 32)" >> .env
echo "JWT_SECRET_KEY=$(openssl rand -hex 32)" >> .env
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env

# 3. (Optional) review what else you can override
cat .env.example

# 4. Boot
docker compose -f docker-compose.minimal.yml up -d --build

# 5. Wait ~60s for migrations + supervisord to settle, then sanity check
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/system/health | jq .
```

The first boot pulls + builds images (`~2 min`), runs alembic
migrations against the 5 per-service databases (`~30 s`), then starts
the 10 inner processes. After the `/system/health` probe returns
`status: healthy` the gateway is accepting traffic on `:8000`.

---

## Parity contract — what minimal mode must do

The whole point of this image is "same security guarantees as prod-ha,
smaller footprint to review." The R0 and R2 behaviours specifically:

| Behaviour | Where it's enforced | How you verify it locally |
|---|---|---|
| `cat /etc/passwd` denied even at `risk_level=low` (R0) | `services/policy/policies/action_semantics_deny.rego` baked into the image | `scripts/qa/test_minimal_mode.sh` runs the R0 destructive matrix |
| Receipt with ed25519 signature + Merkle inclusion | `services/audit/signer.py` + transparency scheduler | `GET /receipts/key` then download a receipt and validate with `tools/aegis_verify/` |
| Tamper-evident chain across days | Daily root signed with `prev_root_hash` | `aegis-verify --bundle dump.json` checks V1-V6 |
| EU AI Act / SOC 2 / NIST evidence bundle | `services/audit/compliance.py` GET `/compliance/export/{bundle_type}` | `curl http://localhost:8000/compliance/export/eu-ai-act?period_start=…&period_end=…` |

Run the parity test:

```bash
cd ../..  # repo root
./scripts/qa/test_minimal_mode.sh
```

The script asserts that the same destructive denials + the same
verifiable-bundle path that pass on prod-ha also pass on the minimal
stack. If they don't, the image is broken — file an issue with the
stdout.

---

## What's NOT in minimal mode

We deliberately strip features the prod-ha topology ships because they
don't earn their keep on a single-host install:

- **Multi-region failover** — minimal mode is one host
- **Read-only DB replica** — minimal mode uses one Postgres
- **Separate Prometheus / Grafana / Jaeger containers** — gateway still
  emits Prometheus metrics on `/metrics` and OTel via env, but the
  visualisation containers don't ship in this image. Point your own
  Grafana / Datadog / CloudWatch at the metrics endpoint.
- **`forensics`, `insight`, `identity_graph`, `flight_recorder`,
  `usage`, `api`, `learning`, `mcp_server` services** — these are
  Sprint 3+ value-adds. Minimal mode runs the 7 inner services that
  the wedge depends on: identity, registry, policy, behavior, decision,
  audit, autonomy. Plus OPA + the gateway.
- **WAF + ALB + ASG** — those are AWS-shaped HA primitives; on a
  self-host install you put your own L7 in front (nginx, Caddy, an
  enterprise WAF).
- **SIEM forwarders, SSO, MCP server, VS Code extension, voice agent** —
  shipped in prod-ha; intentionally absent here. Enable them by
  switching to the full `docker-compose.yml`.

If a feature you need is in that "NOT in minimal" list, run the prod-ha
compose instead. The same code base; just a different topology.

---

## Production hardening (when you outgrow minimal mode)

| When to step up | What to switch to |
|---|---|
| You want one container per service for stricter network policy | `infra/docker-compose.yml` (22-container topology) |
| You want HA / multi-AZ / managed Postgres + Redis | `infra/terraform/environments/prod-ha/` + the 22-container compose |
| You want Kubernetes | `infra/helm/` |

The Aegis image itself is the same in every shape. Only the deployment
fabric around it changes.

---

## Threat model — what minimal mode does not protect against

Honest disclosure:

- A compromised host root can read every secret in `.env` and tamper
  with `/var/lib/postgresql/data`. Self-host = your physical/VM
  security is your responsibility.
- A network attacker reaching `localhost:8181` (OPA) on the host is
  *not* an Aegis bypass — it binds `127.0.0.1` inside the `aegis-core`
  container, unreachable from outside the container by design. If you
  publish it to the host manually, you're rewriting the threat model;
  don't.
- A long-lived JWT issued before a kill-switch trip remains valid until
  it expires. Aegis honours kill switches on the inner decision path,
  so the issued JWT can't `POST /execute` — but cached side-effects
  (Redis quota counters, etc.) keep ticking. Plan your TTLs accordingly.

For everything we *do* protect against, see
`docs/security/threat-scenarios.md`.

---

## Where this lives in the wider Aegis repo

| Path | Purpose |
|---|---|
| `infra/minimal/` | This 3-container self-host topology |
| `infra/docker-compose.yml` | The 22-container prod-ha topology |
| `infra/terraform/environments/prod-ha/` | The HA AWS deployment that runs `ha.aegisagent.in` |
| `infra/helm/` | Kubernetes chart |
| `services/` | The 16 service modules (minimal mode runs 7 of them) |
| `tools/aegis_verify/` | Offline evidence-bundle verifier — works against bundles produced by minimal mode |
| `scripts/qa/test_minimal_mode.sh` | The parity test asserting R0 + R2 behaviour holds |

The image itself is identical to the prod-ha image. The *deployment
shape* is what differs.
