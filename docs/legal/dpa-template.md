# Data Processing Agreement — Template

**Audience:** Customer procurement / privacy counsel + ByteHubble legal.
**Status:** `<LEGAL REVIEW PENDING>` — this is the engineering-drafted skeleton. Legal counsel must finalise §10 (Governing law), §6 (Sub-processors), and the Standard Contractual Clauses annex before counter-signature.
**Version:** 1.1 · 2026-06-20 (refresh for Sprint EI-8 — sub-processor register published; EU-instance residency posture added; MSA cross-reference).
**Companion documents:**
- `docs/legal/msa-template.md` — Master Service Agreement that this DPA attaches to.
- `docs/legal/baa-template.md` — HIPAA-specific overlay for Covered Entities.
- `docs/security/subprocessors.md` — current sub-processor register (incorporated by reference; supersedes the inline §6 list).
- `docs/security/data_residency.md` — per-data-class residency table (referenced in §3 + §5 for the EU instance).
- `docs/security/threat-model.md` — formal STRIDE-per-asset model.
- `docs/security/data_retention.md` — customer-facing retention SLAs referenced in §7.
- `docs/operations/retention-policy.md` — operator-facing retention runbook.
- `docs/operations/incident-response.md` — breach-handling procedure referenced in §8.

> **How to use this template.** Replace every `<CUSTOMER_NAME>`, `<CUSTOMER_LEGAL_ENTITY>`, `<EFFECTIVE_DATE>`, `<JURISDICTION>`, and `<NOTIFICATION_EMAIL>` placeholder. The sub-processor register at `docs/security/subprocessors.md` is now published (per Sprint EI-5; carries the eu-west-1 row for the EU instance). The inline §6 list below is informational; the published register controls in case of conflict. Any deviation from the engineering-set security measures in §5 requires Security-Engineering sign-off recorded in the change log.

---

## 1. Parties

This Data Processing Agreement ("**DPA**") is entered into between:

- **Controller:** `<CUSTOMER_LEGAL_ENTITY>`, having its registered office at `<CUSTOMER_REGISTERED_ADDRESS>` ("**Customer**").
- **Processor:** ByteHubble Technologies Private Limited, having its registered office at `<BYTEHUBBLE_REGISTERED_ADDRESS>` ("**ByteHubble**" or "**Aegis**").

Each a "**Party**" and together the "**Parties**". This DPA is effective from `<EFFECTIVE_DATE>` and is incorporated by reference into the underlying Subscription Agreement between the Parties.

## 2. Definitions

Capitalised terms used but not defined in this DPA carry the meaning set out in the Subscription Agreement. For the avoidance of doubt:

- **"Personal Data"** has the meaning set in Article 4(1) GDPR or its equivalent under the applicable Data Protection Law.
- **"Processing"** has the meaning set in Article 4(2) GDPR.
- **"Sub-processor"** means any third party engaged by ByteHubble to Process Personal Data on Customer's behalf.
- **"Data Protection Law"** means the GDPR, the UK Data Protection Act 2018, the California Consumer Privacy Act, the India Digital Personal Data Protection Act 2023, and any other applicable privacy or data-protection law as identified in §10.
- **"Personal Data Breach"** has the meaning set in Article 4(12) GDPR.
- **"Standard Contractual Clauses"** ("**SCCs**") means the European Commission's June 2021 Standard Contractual Clauses for international Personal Data transfers, attached as Annex II.

## 3. Scope and purpose of Processing

### 3.1 Subject matter

ByteHubble Processes Personal Data on behalf of Customer for the sole purpose of providing the Aegis service: runtime policy enforcement, audit logging, approval routing, and incident response for AI agent activity initiated by or on behalf of Customer.

### 3.2 Nature and purpose

The nature of Processing is automated evaluation of agent tool calls and prompts against Customer-configured policies, the creation of cryptographically signed audit records of every evaluation, and the routing of escalated decisions to Customer-designated human approvers. No use of the Personal Data for ByteHubble's own marketing, model training, or analytics is permitted.

### 3.3 Categories of Personal Data

Personal Data Processed under this DPA is limited to the categories listed in Annex I. By default this comprises:

- Identifiers extracted from agent prompts and tool arguments (names, email addresses, account IDs, IP addresses).
- Operational metadata of Customer's authorised users (Clerk-issued JWT claims).
- Any Personal Data Customer chooses to expose to its agents and which therefore appears in tool arguments or upstream-LLM prompts.

ByteHubble does not request Personal Data beyond what is necessary for service delivery. Customer is responsible for not exposing Personal Data to its agents beyond what is necessary for its lawful business purpose.

### 3.4 Categories of Data Subjects

Customer's employees, contractors, and end-users whose data is acted upon by Customer's AI agents.

### 3.5 Duration

The duration of Processing matches the term of the Subscription Agreement, plus the retention windows set out in §7.

## 4. Processor obligations

ByteHubble shall:

1. Process Personal Data only on documented instructions from Customer, including the standing instruction that the Aegis service constitutes such an instruction for the term of the Subscription Agreement.
2. Ensure that personnel authorised to Process Personal Data are bound by confidentiality.
3. Implement the technical and organisational measures set out in §5.
4. Notify Customer without undue delay if it believes Customer's instructions infringe Data Protection Law.
5. Assist Customer in fulfilling its obligations under Articles 32–36 GDPR (security, breach notification, impact assessments).
6. At Customer's choice, delete or return all Personal Data after the end of the Subscription Agreement, subject to the retention exceptions set out in §7 (signed audit records).
7. Make available all information necessary to demonstrate compliance with this DPA, and allow audits as set out in §9.

## 5. Security measures (technical and organisational)

ByteHubble implements the following security measures. The Customer-facing description below references engineering controls that are code-verifiable in the Aegis source tree at the version stated above; references take the form `path:line` against the `main` branch.

### 5.1 Multi-tenant isolation

Tenant identity is canonicalised at three independent layers:

| Layer | Control | Code reference |
|-------|---------|----------------|
| Identity issuance | Clerk webhook write enforces `aegis_org_id == aegis_tenant_id` at write time. | `services/identity/webhooks_clerk.py:286-290` |
| Token validation | JWT canonicalisation extracts `tenant_id` from the verified claim only. | `sdk/common/clerk_auth.py:26-48` |
| Database constraints | Two Postgres CHECK constraints — `ck_users_org_tenant_match` and `ck_agent_creds_org_tenant_match` — enforce the invariant at write time. | Alembic migration `a1b2c3d4e5f6` |

The `X-Tenant-ID` HTTP header is never trusted as input; tenant scope is always derived from the verified JWT claim and propagated as `request.state.tenant_id` (`services/gateway/_helpers.py:47-62`).

### 5.2 Append-only audit log

Every decision made by Aegis is written to a Postgres `audit_logs` table that is append-only at the storage layer. A `BEFORE UPDATE OR DELETE` trigger raises Postgres exception code `P0001` on any mutation attempt and aborts the transaction (`services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:34-54`). A database administrator with full RDS credentials cannot mutate a row without first dropping the trigger — and that DDL statement is itself a database-level event that is captured by the audit pipeline.

### 5.3 Cryptographic transparency

Each tenant's audit log is summarised daily into a Merkle root that is signed with an ed25519 key (`services/audit/public_transparency.py:70-100`) and published to a public S3 bucket (`s3://aegis-public-roots-628478946931`). Each daily root carries a `prev_root_hash` field linking it to the prior day; the chain runs back to the tenant's genesis root. Any rewrite of history is detectable by any auditor who has archived an earlier root and runs `aegis-verify --root <date>.json --pubkey keys/<signing_kid>.pem`.

### 5.4 Encryption

- **In transit:** TLS 1.2 or higher on every external interface, including the public Aegis API and the SSE event stream.
- **At rest:** AWS-managed encryption at the RDS storage layer (AES-256) and at the S3 storage layer (SSE-S3 or SSE-KMS). Customer-managed key options (BYOK) are tracked under threat-model open item OI-4.

### 5.5 Access control

- Customer access to the Aegis API is gated by Clerk-issued RS256 JWTs (with HS256 legacy support only for `/execute` SDK tokens), validated at `services/gateway/auth.py:190-287`. The dispatcher rejects any HS256 token carrying a Clerk-shaped issuer at `services/gateway/auth.py:239-253` (algorithm-downgrade defence).
- ByteHubble personnel access to the production environment is gated by SSO and multi-factor authentication; all production access is recorded and reviewed.
- Tenant API keys carry a per-request revocation check via `SISMEMBER` against `acp:apikey:revoked` (`services/gateway/_mw_auth.py:31,81`) and take effect on the next call.

### 5.6 Resilience and availability

- The production environment runs on a 2-host Auto Scaling Group behind an Application Load Balancer in `multi_az` configuration (`infra/terraform/environments/prod-ha/main.tf:77,184`).
- Disaster-recovery targets and the most recent drill log are maintained in `docs/operations/disaster-recovery.md`.

### 5.7 Operational telemetry

A per-tenant kill switch can disable Aegis enforcement for a tenant; the switch is gated by operator role, rehydrated into a Redis cache by `services/decision/main.py:59-99`, and enforced on the request path at `services/gateway/middleware.py:441`. Switch toggles are themselves audited.

## 6. Sub-processors

The authoritative sub-processor register is published at `docs/security/subprocessors.md` and updated whenever a vendor changes; Customer is notified of any change that materially expands data exposure via `<NOTIFICATION_EMAIL>` no fewer than thirty (30) days before the change takes effect. Customer may object on reasonable, documented data-protection grounds and ByteHubble shall use commercially reasonable efforts to accommodate or terminate the Sub-processor.

The table below is a snapshot for convenience as of v1.1 of this DPA; in the event of conflict between this table and the published register, the published register controls.

| Sub-processor                  | Role                                       | Region (ap-south-1 instance) | Region (eu-west-1 instance)                 | Personal Data category accessed       |
|--------------------------------|--------------------------------------------|------------------------------|---------------------------------------------|---------------------------------------|
| Amazon Web Services, Inc.      | Infrastructure (compute, storage, network) | `ap-south-1` (Mumbai)        | `eu-west-1` (Ireland), full data isolation  | All categories under §3.3              |
| Clerk, Inc.                    | Customer SSO / JWT issuance                | US (with EU + APAC options)  | EU-pinned Clerk organisation                 | User identifiers under §3.3            |
| Anthropic, PBC                 | Upstream LLM (Path B only, if enabled)     | US (`api.anthropic.com`)     | EU (`api.eu.anthropic.com`) — opt-in         | Prompts and prompt-borne identifiers   |
| OpenAI, L.L.C.                 | Upstream LLM (Path B only, if enabled)     | US (`api.openai.com`)        | Azure OpenAI EU regions — per-tenant DPA     | Prompts and prompt-borne identifiers   |
| Stripe, Inc.                   | Billing / payment processing               | US + EU (auto-routed)        | EU entity for EU customers — no config change | Email, subscription metadata; cards tokenised |
| GitHub, Inc.                   | Source code + CI                           | US                           | US — same (no Customer Data crosses)         | None (build artifacts only)            |
| Sigstore (Fulcio + Rekor)      | Bundle-signing certificate authority + transparency log | Multi-region    | Multi-region — same                          | None (signatures only)                 |
| `<SLACK_OR_PAGERDUTY_VENDOR>`  | Approval-routing notification              | `<REGION>`                   | `<REGION>`                                  | Approval card metadata                 |
| Atlassian (Jira Cloud)         | ITSM (optional, per-tenant)                | Per Atlassian's residency    | Atlassian EU residency available             | Incident summary + Aegis context       |
| ServiceNow                     | ITSM (optional, per-tenant)                | Customer's SNOW instance     | Customer's SNOW instance                     | Incident summary + Aegis context       |

`<LEGAL REVIEW PENDING>` — verify Anthropic / OpenAI inclusion against the Customer's selected Aegis tier. Path A customers do not route prompts through ByteHubble, so the relevant LLM provider is not a sub-processor for them. Atlassian / ServiceNow appear only if Customer has connected the corresponding integration via Settings → Integrations.

Cross-region data flows are governed by `docs/security/data_residency.md`: tenant runtime data never leaves the Customer's chosen Aegis region (`ap-south-1` or `eu-west-1`); only static build artifacts cross regions.

## 7. Data subject rights and retention

### 7.1 Assistance

ByteHubble assists Customer in responding to data-subject requests (access, rectification, erasure, restriction, portability, objection) within the deadlines stipulated by Data Protection Law. Customer initiates requests via `<CUSTOMER_REQUEST_CHANNEL>`; ByteHubble acknowledges within two (2) business days.

### 7.2 Retention windows

The retention windows applied by ByteHubble are documented in `docs/operations/retention-policy.md`. In summary:

| Class                     | Retention                                                                    |
|---------------------------|------------------------------------------------------------------------------|
| Audit logs                | Ten (10) years from creation (regulatory).                                   |
| Operational logs          | Ninety (90) days from creation.                                              |
| Personal Data in usage    | Twenty-four (24) months, then anonymised.                                    |
| Backups                   | Thirty-five (35) days nightly + twelve (12) months monthly.                  |
| Tenant offboarding purge  | Thirty (30) days after termination, certificate of deletion provided.        |

### 7.3 Erasure exception

The audit log forms the cryptographic chain of evidence that underpins the transparency log; Personal Data appearing in audit rows is redacted in place via a separate redaction record that preserves the chain. Audit rows themselves are not deleted before the ten-year window. This is the only erasure exception in the service.

## 8. Personal Data Breach notification

ByteHubble notifies Customer of any Personal Data Breach without undue delay and in any event within seventy-two (72) hours of becoming aware of it. The notification is delivered to `<CUSTOMER_PRIVACY_NOTIFY_EMAIL>` and includes:

- The nature of the Personal Data Breach including the categories and approximate number of Data Subjects and records concerned.
- The likely consequences of the Personal Data Breach.
- The measures taken or proposed to address the Personal Data Breach.
- A point of contact at ByteHubble for further information.

Breach handling follows the procedure in `docs/operations/incident-response.md`. ByteHubble cooperates with any Customer-initiated regulatory filing or data-subject notification that flows from the Personal Data Breach.

## 9. Audit rights

### 9.1 Public artefacts

ByteHubble makes the following artefacts available to Customer without prior request: the public Merkle-root bucket described in §5.3, the status endpoint at `https://aegisagent.in/status`, and the most recent SOC2 Type II report (when available — tracked under threat-model open item OI-1).

### 9.2 On-request audit

Customer or its independent auditor may, at Customer's expense and on no less than thirty (30) days written notice, conduct an audit of ByteHubble's compliance with this DPA, no more than once per calendar year except where a Personal Data Breach has occurred. ByteHubble may require execution of a customary non-disclosure agreement before granting access. The audit shall be conducted during normal business hours and shall not unreasonably interfere with ByteHubble's operations.

### 9.3 Penetration testing

ByteHubble engages an independent penetration-testing vendor in each calendar year (tracked under Track F2 of the v2.0 sprint and reported in the next SOC2 cycle). The resulting report is available under non-disclosure on request.

## 10. International transfers

Where Personal Data is transferred from the European Economic Area, the United Kingdom, or Switzerland to a jurisdiction without an adequacy decision, the Parties incorporate the Standard Contractual Clauses (Module Two: Controller-to-Processor) by reference. The selection of clauses, technical safeguards, and supplementary measures are set out in Annex II.

## 11. Termination

This DPA terminates automatically when the underlying Subscription Agreement terminates. The provisions of §5 (security measures), §7 (retention), and §8 (breach notification) survive termination for the duration of any retention window during which Personal Data is still held.

## 12. Governing law

This DPA is governed by the laws of `<JURISDICTION>`. The Parties submit to the exclusive jurisdiction of the courts of `<JURISDICTION>` for any dispute arising out of or in connection with this DPA.

`<LEGAL REVIEW PENDING>` — the choice of jurisdiction depends on the Customer's home jurisdiction and the location of the Personal Data. Where Customer is established in the EEA, English or Irish law is conventional; for India-resident Customers, Indian courts at `<INDIAN_VENUE>`.

## 13. Order of precedence

In the event of a conflict between this DPA and the Subscription Agreement, this DPA prevails to the extent of the conflict in matters relating to the Processing of Personal Data.

---

## Annex I — Personal Data categories and Processing details

| Field                          | Value                                                                                                  |
|--------------------------------|--------------------------------------------------------------------------------------------------------|
| Categories of Data Subjects    | Customer's employees, contractors, and end-users.                                                       |
| Categories of Personal Data    | Identifiers in agent prompts and tool arguments; Clerk-issued JWT claims; IP addresses; approval metadata. |
| Special categories             | Only if Customer enables Path B for HIPAA-covered operations — see `docs/legal/baa-template.md`.    |
| Nature of Processing           | Automated policy evaluation, cryptographic audit logging, human-approval routing.                       |
| Purpose                        | Runtime governance of Customer's AI agents.                                                             |
| Retention                      | As set out in §7.2.                                                                                    |
| Sub-processors                 | As set out in §6.                                                                                      |

## Annex II — Standard Contractual Clauses

`<LEGAL REVIEW PENDING>` — attach the chosen SCC module(s), the technical and organisational measures table (`<TOM_ANNEX>`), and the docking clause.

---

## Signature

|                          | Customer                  | ByteHubble                |
|--------------------------|---------------------------|---------------------------|
| Signature                | `<SIGNATURE>`             | `<SIGNATURE>`             |
| Name                     | `<CUSTOMER_SIGNATORY>`    | `<BYTEHUBBLE_SIGNATORY>`  |
| Title                    | `<CUSTOMER_TITLE>`        | `<BYTEHUBBLE_TITLE>`      |
| Date                     | `<DATE>`                  | `<DATE>`                  |

---

## Change log

| Version | Date       | Author        | Notes                                                                                |
|---------|------------|---------------|--------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Security Eng  | Engineering-drafted skeleton; awaiting legal review per `<LEGAL REVIEW PENDING>` markers. Closes audit finding C3. |
