# Aegis

Runtime security control plane for autonomous agents. Sits in front of
every agent tool call, applies a policy pipeline, and produces a
cryptographically verifiable audit trail.

* License: Apache 2.0
* Runtime: Python 3.11, FastAPI, PostgreSQL, Redis, OPA
* Deploy target: AWS (ap-south-1 reference deployment) / any Linux + Docker host
* Status: production (single-tenant prod-ha + multi-tenant ready)

## What the gateway does on every request

```
client                                                              upstream tool
  |                                                                       ^
  |  POST /execute  ─────────────────────────────────────────────────────  |
  |                                                                       |
  +-> 0. Kill switch     tenant-wide blockade                              |
      1. Auth            JWT/API-key + jti revocation                      |
      2. Rate limit      per-token / per-agent / per-tenant                |
      3. Inference       prompt-injection + tool-shape risk score          |
      4. Canonical       normalise tool args to action shape               |
      5. Policy          OPA (slow) / local action semantics (fast)        |
      6. Behavior        per-minute window + cumulative risk pipeline      |
      7. Decision        allow | monitor | escalate | deny | quarantine    |
      8. Enforce         pass-through or return 403 + structured findings  |
      9. Filter          redact secrets from response body                 |
     10. Audit           chained ed25519-signed receipt to Redis stream    |
```

## Quick start (local Docker stack, ~3 minutes)

```bash
git clone https://github.com/Abhi-mishra998/aegis.git
cd aegis
cp .env.example infra/.env          # fill in JWT_SECRET_KEY, INTERNAL_SECRET
cd infra
docker compose -f docker-compose.yml up -d
docker compose ps                   # 20+ services, expect all "healthy"
```

UI at `http://localhost:8080`. Admin login: `admin@acp.local` / `admin1234`
(seeded by `scripts/utils/seed_admin.py`; rotate before exposing the
stack).

## SDK quick start

```bash
pip install aegis-anthropic aegis-openai aegis-langchain
```

Wrap your existing SDK client — every tool call routes through Aegis
before reaching the provider:

```python
from aegis_anthropic import AegisAnthropic

client = AegisAnthropic(
    aegis_key="acp_…",
    aegis_url="https://your-aegis-host",
    tenant_id="…",
    agent_id="…",
    api_key="sk-ant-…",       # your Anthropic key
)
resp = client.messages.create(model="claude-sonnet-4-5", tools=[…], messages=[…])
```

Same shape for `aegis-openai` and `aegis-langchain`. See
[`docs/integrations/sdk-wrappers.md`](docs/integrations/sdk-wrappers.md).

## Verifying an evidence bundle (offline, no Aegis account)

The audit chain format is published as the open spec **AEVF (Aegis
Evidence Verification Format) `aevf/0.1.0`**. An auditor downloads any
evidence bundle Aegis exports and verifies it without contacting our
infrastructure:

```bash
pip install aegis-aevf
aegis-verify --bundle bundle.json
```

The verifier runs six independent checks (V1–V6). Spec, auditor
checklist, and a deterministic reference bundle:
[`docs/AEVF/`](docs/AEVF/).

## Documentation

Canonical references at the root:

| File | Scope |
|---|---|
| [`agies-bussiness.md`](agies-bussiness.md) | What Aegis is, what it isn't, live evidence, gaps — context briefing v1.3.0 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Service map, request pipeline, data model |
| [`API.md`](API.md) | HTTP surface — auth, /execute, /storylines, /iag, /remediation, /threat-intel |
| [`SECURITY.md`](SECURITY.md) | Threat model, crypto, secret handling, vuln reporting |
| [`docs/operations/deployment.md`](docs/operations/deployment.md) | AWS reference deploy + local Docker |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, branch model, test gates |
| [`CHANGELOG.md`](CHANGELOG.md) | Versioned changes |

Deeper reference under [`docs/`](docs/) (GitBook layout; see
[`docs/SUMMARY.md`](docs/SUMMARY.md) for the table of contents).

### Procurement & audit docs

For CISOs, Principal Security Architects, and customer privacy counsel:

| File | Scope |
|---|---|
| [`docs/security/threat-model.md`](docs/security/threat-model.md) | Formal STRIDE-per-asset model, top-10 ranked threats with file:line mitigation citations |
| [`docs/security/dpa-template.md`](docs/security/dpa-template.md) | Data Processing Agreement template (engineering-drafted, legal review pending) |
| [`docs/security/baa-template.md`](docs/security/baa-template.md) | HIPAA Business Associate Agreement template (engineering-drafted, legal review pending) |
| [`docs/operations/incident-response.md`](docs/operations/incident-response.md) | Sev-0..3 classes, 72-hour customer-notify SLO, 14-day postmortem SLA |
| [`docs/operations/retention-policy.md`](docs/operations/retention-policy.md) | 10-year audit / 90-day op-log / 24-month PII / 30-day offboarding windows |
| [`docs/operations/disaster-recovery.md`](docs/operations/disaster-recovery.md) | Customer-facing RTO 4h / RPO 15m posture + drill log |

## Repository layout

```
services/        12 FastAPI microservices (gateway is the entry point)
sdk/             shared internal Python helpers
integrations/    aegis-anthropic / aegis-openai / aegis-langchain / aegis-bedrock SDKs
tools/           aegis_verify (publishes as aegis-aevf on PyPI)
ui/              React + Vite admin console (served by nginx)
voice-agent/     LiveKit-based voice guide (separate deployment)
infra/           docker-compose + terraform (modules + environments/{dev,prod-ha})
tests/           pytest suites — security/, policy/, eval/, integration/
docs/            GitBook reference documentation
```

## Production reference deployment

The published reference is in [`infra/terraform/environments/prod-ha/`](infra/terraform/environments/prod-ha/):

* Region: `ap-south-1` (Mumbai)
* 2× `m6g.medium` EC2 in an ASG behind a multi-AZ ALB
* RDS PostgreSQL `db.t3.small` Multi-AZ
* ElastiCache Redis (2 nodes, automatic failover enabled)
* NAT gateway, S3 + DynamoDB VPC gateway endpoints
* CloudTrail multi-region trail, S3 default encryption
* AWS Secrets Manager for runtime credentials
* AWS KMS customer key for receipt-signing envelope encryption

Build/deploy is `docker compose` for local, Terraform + SSM for the
prod-ha rollout. See [`docs/operations/deployment.md`](docs/operations/deployment.md).

## License

Apache 2.0. See [LICENSE](LICENSE).

Security disclosures: [SECURITY.md](SECURITY.md).
