# SOC 2 Type I — Evidence Tracker

> Sprint 9 starting point. This file maps each in-scope Trust Services
> Criterion to the Aegis evidence that proves it. The vendor (Vanta /
> Drata / Secureframe-class) pulls these references into their
> control library during the Type I engagement. Update inline as
> evidence lands.

**Status**: ENGAGED — vendor selection in progress (Q3 2026 target).
Type II follows ~6 months after Type I attestation.

---

## Common Criteria (CC) — Security

| Control | Description | Aegis evidence |
|--------|-------------|----------------|
| CC1.1 | Commitment to integrity + ethical values | `SECURITY.md`, `CONTRIBUTING.md`, vendor-onepager.md |
| CC2.1 | Board oversight of security | Sole-founder + board meeting cadence (vendor questionnaire) |
| CC3.1 | Risk assessment process | `AUDIT_REPORT.md` (forensic due-diligence) + quarterly review minutes |
| CC4.1 | Monitoring activities | Prometheus + CloudWatch alarms, AlertManager rules in `infra/alertmanager/` |
| CC5.1 | Control activities for risks | The 11-stage gateway pipeline + Sprint 5 attack evaluation suite |
| CC6.1 | Logical access — provisioning | IAM roles in `infra/terraform/modules/iam`; admin access via SSO |
| CC6.2 | Logical access — authentication | JWT (RS256/HS256), API keys, MFA on console — `services/identity/oidc.py` |
| CC6.3 | Logical access — authorisation | OPA-evaluated policies + autonomy contracts |
| CC6.6 | Logical access — system protection | Sprint 9 prod-ha private subnets, NAT, WAFv2, security groups |
| CC6.7 | Restrict transmission | TLS 1.2+ ALB-enforced, in-transit Redis encryption (Sprint 9) |
| CC6.8 | Malicious software prevention | Container image scanning in CI (Sprint 9 follow-up: trivy in GH Actions) |
| CC7.1 | Detect / respond to vulnerabilities | RFC 9116 disclosure, security@aegisagent.in, 48h SLA |
| CC7.2 | Monitor system components | Sprint 4 fleet dashboards + Sprint 5 evaluation drift alerts |
| CC7.3 | Evaluate security incidents | `docs/runbooks/incident_response.md` (Sprint 9 follow-up) |
| CC7.4 | Implement incident response | Pager rotation + PagerDuty escalation (TBD) |
| CC7.5 | Recover from incidents | `docs/runbooks/dr.md` — weekly drill + signed evidence |
| CC8.1 | Authorize / change management | Branch protection on `main`, PR review required, terraform changes via `workflow_dispatch` only |
| CC9.1 | Risk mitigation | Sprint 9 prod-ha environment + KMS hardening + pen-test |
| CC9.2 | Vendor + business-partner risk | `docs/security/vendor-onepager.md` + sub-processor list (TBD) |

## Availability

| Control | Description | Aegis evidence |
|--------|-------------|----------------|
| A1.1 | Performance monitoring | Sprint 4 KPIs, Sprint 5 evaluation drift, OTel exporter (Sprint 8) |
| A1.2 | Backup + recovery | `scripts/ops/backup.sh` + `dr_evidence.py` weekly drill |
| A1.3 | Disaster recovery | `docs/runbooks/dr.md` — documented RTO/RPO |

## Confidentiality

| Control | Description | Aegis evidence |
|--------|-------------|----------------|
| C1.1 | Identify confidential information | Output filter (Sprint 2 — PII/email/Aadhaar redaction) |
| C1.2 | Confidentiality during disposal | S3 lifecycle policies + KMS key revocation runbook |

## Processing Integrity (optional)

| Control | Description | Aegis evidence |
|--------|-------------|----------------|
| PI1.1 | Inputs are complete + valid | OPA policy evaluation + behavior firewall + transactional outbox |
| PI1.2 | Outputs are complete + valid | Cryptographically signed receipts + Merkle transparency root |

## Privacy (optional — likely deferred to Type II)

| Control | Description | Aegis evidence |
|--------|-------------|----------------|
| P1.1 | Notice + communication | Privacy policy at aegisagent.in/privacy (TBD) |

---

## Open evidence items (Sprint 9 will close)

- [x] KMS-enforced signing keys with prod-guard refusal of disk fallback
- [x] Multi-AZ RDS + replication-group Redis + ASG private-subnet stack
- [x] DR evidence artifact (signed JSON, weekly cron)
- [x] WAFv2 with managed rules + per-IP rate limit
- [x] RFC 9116 security.txt
- [x] CycloneDX SBOM with signature
- [ ] Container image vulnerability scan in CI (Sprint 9 follow-up)
- [ ] Incident-response runbook (Sprint 9 follow-up)
- [ ] Pen-test engagement letter + scope-of-work signed
- [ ] Vendor SOC 2 platform onboarding (Vanta / Drata / Secureframe)

The two follow-ups are tracked in `SPRINT_9_REPORT.md` §10 follow-ups.
