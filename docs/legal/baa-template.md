# Business Associate Agreement — Template

**Audience:** HIPAA-Covered-Entity customer privacy counsel + ByteHubble legal.
**Status:** `<LEGAL REVIEW PENDING>` — engineering-drafted skeleton. Legal counsel must finalise §13 (Governing law / venue), §3 (Permitted Uses) against the Customer's own minimum-necessary scope, and the breach-notification window in §7 against state-law overlays.
**Version:** 1.1 · 2026-06-20 (refresh for Sprint EI-8 — companion-doc paths aligned with the new docs/legal/ canonical location).
**Companion documents:**
- `docs/legal/msa-template.md` — Master Service Agreement that this BAA attaches to.
- `docs/legal/dpa-template.md` — base Data Processing Agreement; this BAA is the HIPAA-specific overlay.
- `docs/security/data_retention.md` — customer-facing retention SLAs referenced in §11.
- `docs/operations/retention-policy.md` — operator-facing retention runbook.
- `docs/operations/incident-response.md` — breach-handling procedure referenced in §7.

> **How to use this template.** Replace every `<COVERED_ENTITY_NAME>`, `<EFFECTIVE_DATE>`, `<JURISDICTION>`, `<NOTIFICATION_EMAIL>` placeholder. Confirm with the Customer that they are a Covered Entity under 45 C.F.R. § 160.103 and that ByteHubble is being engaged as a Business Associate. This BAA is in addition to, not in substitution for, the Data Processing Agreement at `docs/legal/dpa-template.md`.

---

## 1. Parties

This Business Associate Agreement ("**BAA**") is entered into between:

- **Covered Entity:** `<COVERED_ENTITY_NAME>`, a `<COVERED_ENTITY_TYPE — health plan / health care provider / health care clearinghouse>` ("**Covered Entity**").
- **Business Associate:** ByteHubble Technologies Private Limited ("**Business Associate**" or "**ByteHubble**").

Each a "**Party**" and together the "**Parties**". This BAA is effective from `<EFFECTIVE_DATE>`.

## 2. Definitions

Capitalised terms used but not otherwise defined in this BAA have the meaning set in the Health Insurance Portability and Accountability Act of 1996 ("**HIPAA**") and its implementing regulations, as amended by the Health Information Technology for Economic and Clinical Health Act ("**HITECH**") and the HIPAA Omnibus Final Rule. In particular:

- **"Protected Health Information"** ("**PHI**") has the meaning set in 45 C.F.R. § 160.103, limited to PHI created, received, maintained, or transmitted by Business Associate on behalf of Covered Entity in connection with the services described in §3.
- **"Electronic Protected Health Information"** ("**ePHI**") has the meaning set in 45 C.F.R. § 160.103.
- **"Breach"** has the meaning set in 45 C.F.R. § 164.402.
- **"Security Incident"** has the meaning set in 45 C.F.R. § 164.304.
- **"Subcontractor"** has the meaning set in 45 C.F.R. § 160.103.
- **"Designated Record Set"** has the meaning set in 45 C.F.R. § 164.501.

## 3. Permitted uses and disclosures of PHI

### 3.1 Service purpose

Business Associate may use and disclose PHI only as necessary to provide the Aegis service to Covered Entity: runtime policy enforcement, audit logging, approval routing, and incident response for AI-agent activity initiated by Covered Entity. No other use is permitted.

### 3.2 Minimum-necessary

Business Associate makes reasonable efforts to limit access to PHI to the minimum necessary to perform the service, in accordance with 45 C.F.R. § 164.514(d). In particular:

- Aegis policy engine evaluates tool calls and prompts against pre-configured policies; the engine processes only the fields necessary to match a policy rule.
- Approval routing exposes to the approver only the metadata required to make the approval decision, not the underlying PHI payload, except where Covered Entity has explicitly enabled payload disclosure for a given approval card.
- Sub-contractors (§9) access PHI only on the minimum-necessary basis described in their own respective BAAs.

### 3.3 Other permitted uses

Business Associate may use PHI:

1. For the proper management and administration of Business Associate, including system maintenance and quality assurance, only to the extent that such uses are required by law or that any disclosure to a third party is on receipt of reasonable assurances from that third party that the PHI will be held confidentially and used only as required by law.
2. To provide data-aggregation services relating to the health-care operations of Covered Entity as defined in 45 C.F.R. § 164.501.

Business Associate shall not use or disclose PHI for marketing, sale, or model training purposes.

### 3.4 De-identification

Business Associate may de-identify PHI in accordance with 45 C.F.R. § 164.514(a)–(c). De-identified data is no longer PHI and is not subject to this BAA.

## 4. Safeguards

Business Associate implements administrative, physical, and technical safeguards in compliance with 45 C.F.R. §§ 164.308, 164.310, 164.312, and 164.316. The engineering controls below are code-verifiable on the `main` branch at the version stated above; references take the form `path:line`.

### 4.1 Administrative safeguards (§ 164.308)

- A designated Security Official is named in `docs/operations/incident-response.md`.
- Workforce-clearance and role-based access procedures align with ByteHubble's internal security policy. All workforce members are bound by confidentiality.
- A Security Incident Response Plan is documented in `docs/operations/incident-response.md`, with severity classes Sev-0 through Sev-3 and named runbook owners.
- A Sanction Policy applies to workforce members who fail to comply with this BAA.

### 4.2 Physical safeguards (§ 164.310)

PHI is stored only in AWS data centres that maintain SSAE-18 SOC 2 / ISO 27001 certifications. ByteHubble personnel do not maintain a customer-PHI data centre of their own.

### 4.3 Technical safeguards (§ 164.312)

| Requirement                                | Implementation                                                                                                                                                                  |
|--------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Access control (§ 164.312(a))              | Clerk-issued RS256 JWT validation at `services/gateway/auth.py:190-287`; algorithm-downgrade defence at `:239-253`; per-request API-key revocation via `SISMEMBER` at `services/gateway/_mw_auth.py:31,81`. |
| Audit controls (§ 164.312(b))              | Every policy decision is written to a Postgres `audit_logs` table that is append-only at the storage layer (`services/audit/alembic/versions/3a519b48a6f2_audit_log_append_only_trigger.py:34-54`). |
| Integrity (§ 164.312(c))                   | Daily Merkle root of each tenant's audit log is signed with an ed25519 key and published to a public S3 bucket with `prev_root_hash` chaining (`services/audit/public_transparency.py:70-100`). Any tampering with a past audit row invalidates every subsequent daily root. |
| Authentication (§ 164.312(d))              | Clerk-issued RS256 JWT (Customer SSO) or HS256 API key (`/execute` SDK tokens). Multi-factor authentication is required for all ByteHubble production access.                  |
| Transmission security (§ 164.312(e))       | TLS 1.2 or higher on every external interface (API and SSE event stream). At-rest encryption via AWS-managed AES-256 for RDS and SSE-S3 or SSE-KMS for S3.                                                  |

### 4.4 Tenant isolation

Tenant scope is enforced at three layers — Clerk webhook write, JWT canonicalisation, and Postgres CHECK constraints (`ck_users_org_tenant_match` and `ck_agent_creds_org_tenant_match`, migration `a1b2c3d4e5f6`). The `X-Tenant-ID` HTTP header is never read from the client; tenant scope is always derived from the verified JWT claim via `request.state.tenant_id` (`services/gateway/_helpers.py:47-62`).

## 5. Reporting obligations

Business Associate reports to Covered Entity:

1. **Breach** — within seventy-two (72) hours of discovery, in accordance with 45 C.F.R. § 164.410. Discovery is defined per § 164.410(a)(2).
2. **Security Incident** — successful unauthorised access, use, disclosure, modification, or destruction of ePHI or interference with system operations is reported within the seventy-two-hour window in (1). Unsuccessful attempts (e.g., scanning probes, rate-limited brute-force) are reported in an aggregated quarterly report rather than individually, in accordance with the standard interpretation of § 164.314(a)(2)(i)(C).
3. **Use or disclosure not provided for by this BAA** — within seventy-two (72) hours of discovery.

The reports include the information required by 45 C.F.R. § 164.410(c), to the extent available, and are delivered to `<COVERED_ENTITY_NOTIFICATION_EMAIL>`. Business Associate's incident-response procedure is documented in `docs/operations/incident-response.md`.

## 6. Mitigation

Business Associate mitigates, to the extent practicable, any harmful effect that is known to Business Associate of a use or disclosure of PHI in violation of this BAA. The mitigation steps for each incident class are recorded in the post-incident report under the runbook in `docs/operations/incident-response.md`.

## 7. Breach-notification timing

| Trigger                                  | Window                                                |
|------------------------------------------|-------------------------------------------------------|
| Notify Covered Entity of a Breach        | Within 72 hours of Business Associate's discovery.    |
| Provide details required by § 164.410(c) | Together with initial notification, to the extent known; supplemented within 30 days as further details emerge. |
| Cooperate with HHS / OCR notification    | On Covered Entity's request, without unreasonable delay. |

`<LEGAL REVIEW PENDING>` — verify that the 72-hour window does not conflict with state-law overlays applicable to Covered Entity (e.g., California, New York, Texas). Where state law is stricter, the stricter window applies.

## 8. Access, amendment, and accounting

### 8.1 Access (§ 164.524)

Business Associate provides PHI within a Designated Record Set held by Business Associate on Covered Entity's behalf to Covered Entity, or as directed by Covered Entity to an Individual, within fifteen (15) business days of Covered Entity's written request, to enable Covered Entity to meet its access obligation under 45 C.F.R. § 164.524.

### 8.2 Amendment (§ 164.526)

Business Associate makes PHI in a Designated Record Set held by Business Associate on Covered Entity's behalf available for amendment, and incorporates amendments to PHI as directed by Covered Entity, within fifteen (15) business days of Covered Entity's written request.

### 8.3 Accounting of disclosures (§ 164.528)

Business Associate maintains an accounting of disclosures of PHI to enable Covered Entity to respond to a request for an accounting under § 164.528. By virtue of the append-only audit log described in §4.3, every disclosure of PHI by Aegis policy or routing logic is captured in an audit row that survives for the retention window in §11.

## 9. Subcontractors

In accordance with 45 C.F.R. § 164.502(e)(1)(ii), Business Associate ensures that any Subcontractor that creates, receives, maintains, or transmits PHI on behalf of Business Associate agrees in writing to the same restrictions and conditions that apply to Business Associate under this BAA. The Subcontractor list is the same as the sub-processor list in `docs/legal/dpa-template.md` §6, restricted to the sub-set that may access PHI:

| Subcontractor              | Role                                                          | PHI category accessed                            |
|----------------------------|---------------------------------------------------------------|--------------------------------------------------|
| Amazon Web Services, Inc.  | Infrastructure (RDS, S3, ALB, ASG).                          | All categories under §3.                          |
| Clerk, Inc.                | Customer SSO / JWT issuance.                                 | Identifiers only — no clinical PHI.               |
| Anthropic / OpenAI (Path B only) | Upstream LLM, only if Covered Entity has signed a separate BAA with that vendor. | Prompts and prompt-borne PHI.                     |

`<LEGAL REVIEW PENDING>` — confirm Anthropic / OpenAI BAA status for the Covered Entity's chosen Path. Covered Entity should not enable Path B for PHI traffic without a directly executed BAA with the upstream LLM vendor.

## 10. Return or destruction at termination

On termination of the underlying Subscription Agreement, Business Associate, at Covered Entity's option, returns to Covered Entity or destroys all PHI received from or created on behalf of Covered Entity that Business Associate maintains, in accordance with 45 C.F.R. § 164.504(e)(2)(ii)(J). The retention exception described in §11.2 applies.

If return or destruction is infeasible for any subset of PHI, Business Associate extends the protections of this BAA to that PHI and limits further uses and disclosures of the PHI to those purposes that make the return or destruction infeasible, for so long as Business Associate maintains the PHI.

## 11. Retention

### 11.1 Standard windows

Retention windows are documented in `docs/operations/retention-policy.md`. For PHI specifically:

| Class                              | Retention                                                        |
|------------------------------------|------------------------------------------------------------------|
| Audit logs containing PHI references | Ten (10) years from creation.                                  |
| PHI in operational logs             | Ninety (90) days from creation.                                 |
| PHI in usage records                | Twenty-four (24) months, then de-identified.                    |
| Backups                             | Thirty-five (35) days nightly + twelve (12) months monthly.     |
| Offboarding purge                   | Thirty (30) days after termination, certificate of destruction. |

### 11.2 Audit-log exception

The audit log is the cryptographic chain of evidence that underpins HIPAA § 164.312(b) audit controls. PHI references appearing in an audit row are redacted in place via a separate redaction record that preserves the chain (sha-256 hash of the redacted field is retained for integrity verification). Audit rows themselves are not deleted before the ten-year window.

This is the only carve-out from the §10 return-or-destruction obligation. Covered Entity is notified of the carve-out at contracting time so it can include the exception in its own retention schedule.

## 12. Covered Entity obligations

Covered Entity:

1. Informs Business Associate of any limitation in Covered Entity's notice of privacy practices that may affect Business Associate's use or disclosure of PHI.
2. Informs Business Associate of any change in, or revocation of, an Individual's permission to use or disclose PHI, to the extent that such change may affect Business Associate's use or disclosure of PHI.
3. Does not request Business Associate to use or disclose PHI in any manner that would not be permissible under the Privacy Rule if done by Covered Entity, except where data-aggregation or management-and-administration uses are permitted as per § 164.504(e).

## 13. Term and termination

### 13.1 Term

This BAA is effective on `<EFFECTIVE_DATE>` and remains in effect for so long as Business Associate creates, receives, maintains, or transmits PHI on behalf of Covered Entity under the Subscription Agreement, or for so long as PHI is retained under §11, whichever is longer.

### 13.2 Termination for cause

Either Party may terminate this BAA on thirty (30) days written notice if the other Party materially breaches this BAA and fails to cure the breach within that period. If cure is not feasible, the non-breaching Party may terminate immediately and report the breach to HHS in accordance with 45 C.F.R. § 164.504(e)(1)(ii).

### 13.3 Effect of termination

The provisions of §4 (safeguards), §10 (return or destruction), §11 (retention), and §6 (mitigation) survive termination for the period in which any PHI is still held by Business Associate.

## 14. Governing law

This BAA is governed by the laws of `<JURISDICTION>` and the federal HIPAA regulations.

`<LEGAL REVIEW PENDING>` — for U.S. Covered Entities, default to the law of the Covered Entity's state of incorporation; verify whether state law imposes stricter breach-notification timing than federal HIPAA.

---

## Signature

|                          | Covered Entity            | ByteHubble                |
|--------------------------|---------------------------|---------------------------|
| Signature                | `<SIGNATURE>`             | `<SIGNATURE>`             |
| Name                     | `<COVERED_ENTITY_SIGNATORY>` | `<BYTEHUBBLE_SIGNATORY>` |
| Title                    | `<COVERED_ENTITY_TITLE>`  | `<BYTEHUBBLE_TITLE>`      |
| Date                     | `<DATE>`                  | `<DATE>`                  |

---

## Change log

| Version | Date       | Author        | Notes                                                                                              |
|---------|------------|---------------|----------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Security Eng  | Engineering-drafted HIPAA overlay; awaiting legal review per `<LEGAL REVIEW PENDING>` markers. Closes audit finding C4. |
