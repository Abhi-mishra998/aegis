# Incident Response

**Audience:** ByteHubble engineering on-call, customer security teams, auditors.
**Owner:** ByteHubble SRE + Security Engineering.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/operations/disaster-recovery.md` — DR procedures invoked for Sev-0/1 platform outages.
- `docs/operations/retention-policy.md` — retention windows that bound incident-evidence handling.
- `docs/runbooks/audit_chain_violation.md`, `docs/runbooks/dr.md`, `docs/runbooks/key_rotation.md`, `docs/runbooks/restore_drill.md`, `docs/runbooks/tenant_data_request.md` — the per-trigger runbooks invoked from §5.

This file establishes how ByteHubble detects, classifies, contains, and communicates incidents on the Aegis platform. It is the document referenced from the customer-facing DPA §8 (breach notification) and BAA §5 (HIPAA Security Incident reporting).

---

## 1. Severity levels

Severity is assigned by the first responder on triage and may be raised by anyone in the escalation chain. It is never lowered without Security-Eng sign-off. Response-time SLOs are commitments to the customer; internal containment work continues beyond the SLO until the incident is closed.

| Sev   | Definition                                                                                                                                                                       | Examples                                                                                                                                          | First-response SLO (acknowledged) | Status-update SLO              | Customer notify  |
|-------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------|--------------------------------|------------------|
| Sev-0 | Confirmed compromise of customer data, the audit chain, or the signing keys — or a complete loss of policy enforcement.                                                          | Append-only trigger raises (`docs/runbooks/audit_chain_violation.md`); Merkle key suspected compromised; cross-tenant data leak confirmed.        | 15 minutes 24/7                   | Every 60 minutes               | Within 72 h (HIPAA), without undue delay (GDPR). |
| Sev-1 | Major service degradation: gateway error rate > 1% sustained, full-region outage, or a single-tenant outage of the Aegis enforcement path.                                       | ALB target health flapping > 5 min; OPA evaluation timeouts on > 1% of `/execute` calls; kill switch incorrectly engaged.                          | 30 minutes 24/7                   | Every 2 hours                  | Within 24 h.     |
| Sev-2 | Partial degradation that does not block enforcement: SSE feed stale, behavior firewall in degraded mode, observability dashboard outage.                                          | Per-tenant `degraded_mode_policy` engages and stays engaged > 30 min; `/status` reports a component non-operational.                              | 4 hours business hours            | Daily until resolved           | Best-effort.     |
| Sev-3 | Single-instance defect, cosmetic bug, or chronic minor latency drift that has a workaround.                                                                                       | UI panel renders stale data; one canary p95 > target without customer-side impact.                                                                | Next business day                 | Weekly                         | None unless asked. |

A change in customer-visible impact during the incident triggers a re-classification, recorded in the incident timeline.

---

## 2. On-call rota and escalation chain

### 2.1 Rota

| Role                          | Who                              | Hours              | Tool                                |
|-------------------------------|----------------------------------|--------------------|-------------------------------------|
| Primary on-call (Tier 1)      | SRE rotation                     | 24/7 / weekly hand-off | PagerDuty + Slack `#aegis-incidents` |
| Secondary on-call (Tier 2)    | Security-Eng rotation            | 24/7 / weekly hand-off | PagerDuty + Slack                   |
| Incident commander            | Senior on-call (named per incident) | Activated for Sev-0 / Sev-1 | PagerDuty + Zoom war-room          |
| Executive sponsor             | CTO                              | Sev-0 only         | Phone                                |
| Customer-comms owner          | Head of Customer Trust            | Sev-0 / Sev-1     | Email + customer Slack Connect       |
| Legal & privacy counsel       | General counsel                   | Sev-0 with regulatory dimension | Phone + email                       |

The pager rota is maintained in PagerDuty and is reviewed weekly at the SRE stand-up.

### 2.2 Escalation chain

```
   Tier 1 (SRE on-call)
        │  ack < 15 min for Sev-0 / 30 min for Sev-1
        ▼
   Tier 2 (Security-Eng on-call)
        │  pulled in for any Sev-0, any Sev-1 with security dimension
        ▼
   Incident Commander
        │  assigned at Sev-0 declaration; runs the war-room
        ▼
   Executive Sponsor (CTO) + Customer-Comms + Legal
        │  notified at Sev-0 declaration
```

Hand-offs across shift boundaries are recorded in the incident timeline. The incoming on-call reads the timeline before taking the pager.

---

## 3. Communication policy

### 3.1 Internal channels

- **`#aegis-incidents`** (Slack) — the canonical live channel for any Sev-0 / Sev-1. The incident commander posts there; everyone else watches.
- **Zoom war-room** — opened at Sev-0 declaration; link posted in `#aegis-incidents`. The commander runs it.
- **PagerDuty incident** — opened automatically on alert fire or manually by the first responder. Status updates land here as well as in Slack.

### 3.2 Customer notification

| Sev   | Channel                                                | Initial content                                                  | Follow-up cadence      |
|-------|--------------------------------------------------------|------------------------------------------------------------------|------------------------|
| Sev-0 | Email to the customer's privacy-notify address; Slack Connect if configured. | Nature of the incident, categories of data potentially affected, immediate containment steps, point of contact. Aligned to GDPR Art. 33 (3) and HIPAA § 164.410 (c). | Every 24 h until close; final report within 14 days of close. |
| Sev-1 | Email to the customer's operational-notify address; status page (`https://aegisagent.in/status`). | Service impact, mitigation, estimated time to recovery.          | Every 4 h until close.  |
| Sev-2 | Status page only.                                       | Component name, observed symptom, mitigation.                    | On change.              |
| Sev-3 | Optional. Captured in the next release-notes.           | n/a                                                              | n/a                     |

Customer notification ownership rests with the Head of Customer Trust; legal review is required for any Sev-0 notification before send.

### 3.3 Regulatory notification

When a Sev-0 is also a Personal Data Breach under GDPR Art. 4(12) or a Breach under HIPAA § 164.402, ByteHubble cooperates with the Customer's regulatory filing (GDPR 72-hour supervisory-authority notification; HIPAA HHS / OCR reporting). The cooperation is the substance of DPA §8 and BAA §5 / §7.

### 3.4 External advisories

Any vulnerability disclosure that affects multiple customers is published as an advisory at `https://aegisagent.in/advisories` with a CVE identifier where applicable. The advisory is mirrored to the customer email list at the time of publication.

---

## 4. Detect → contain → eradicate → recover

The standard NIST 800-61 phases apply. Each Sev-0 / Sev-1 follows this loop:

1. **Detect.** Alert fires (Alertmanager, customer report, internal observation). First responder acknowledges in PagerDuty within the §1 SLO.
2. **Triage and classify.** Severity assigned per §1, runbook identified per §5, incident commander appointed if Sev-0.
3. **Contain.** Stop the bleed. For data-plane incidents, the per-tenant kill switch (`services/decision/main.py:59-99`; enforced at `services/gateway/middleware.py:441`) is the primary containment mechanism. For audit-chain violations, all writes pause per `docs/runbooks/audit_chain_violation.md`.
4. **Eradicate.** Remove the root cause: revoke compromised credentials, ship the patch, drop and recreate the broken policy bundle, etc.
5. **Recover.** Re-enable enforcement, verify `/status` healthy, run the post-recovery verification matrix (Merkle-chain verification, smoke test of `/execute`, behavior-firewall consult test).
6. **Post-incident review.** Postmortem within 14 days per §6.

Every step is recorded in the incident timeline with timestamp and operator name. The timeline becomes Annex A of the postmortem.

---

## 5. Per-severity runbook index

The runbooks below are the operational scripts on-call follows. They are linked from §1 by trigger.

| Trigger                                                                                       | Runbook                                                          | Sev   |
|-----------------------------------------------------------------------------------------------|------------------------------------------------------------------|-------|
| `ChainViolationImmediate` Alertmanager alert                                                  | `docs/runbooks/audit_chain_violation.md`                         | Sev-0 |
| Region failure / multi-AZ failover suspected                                                  | `docs/runbooks/dr.md`                                            | Sev-0 / Sev-1 |
| Backup restore needed                                                                         | `docs/runbooks/restore_drill.md`                                 | Sev-1 |
| Suspected compromise of any signing key (JWT HS256, ed25519 Merkle, Clerk)                    | `docs/runbooks/key_rotation.md`                                  | Sev-0 |
| Tenant data-subject access / erasure request                                                  | `docs/runbooks/tenant_data_request.md`                           | Sev-2 (operational) |
| Behavior firewall outage / degraded-mode policy stays engaged > 30 min                        | inline §4 procedure; pull Security-Eng                            | Sev-2 |
| Customer reports cross-tenant read                                                            | inline §4 procedure + immediate Sev-0 declaration                | Sev-0 |

Each runbook documents (a) the trigger, (b) the verification it actually fired, (c) the containment command, (d) the restore-of-service command, (e) the evidence to capture for the postmortem.

When a new incident type is encountered, a draft runbook is added to `docs/runbooks/` within the same sprint and linked from this index.

---

## 6. Postmortem template and 14-day SLA

A postmortem is written within fourteen (14) days of incident close for every Sev-0 and Sev-1. Sev-2 postmortems are written when the on-call requests one or when the same trigger fires twice in a quarter. Sev-3 incidents do not require a postmortem.

The template follows the blameless-postmortem convention. The file lives at `docs/postmortems/INC-YYYY-MM-DD-<slug>.md`.

```markdown
# Postmortem: <one-line incident description>

| Field                | Value                                                                              |
|----------------------|------------------------------------------------------------------------------------|
| Incident ID          | INC-YYYY-MM-DD-<slug>                                                              |
| Severity             | Sev-0 / Sev-1 / Sev-2                                                              |
| Date / time detected | YYYY-MM-DD HH:MM UTC                                                               |
| Date / time resolved | YYYY-MM-DD HH:MM UTC                                                               |
| Time-to-detect       | mm:ss from first symptom to first alert ack                                        |
| Time-to-recover      | mm:ss from first ack to /status returning operational                              |
| Customers affected   | List of tenant_ids OR "all" OR "none observed"                                     |
| Data affected        | Categories per DPA §3.3; or "none"                                                 |
| Author               | <Name>                                                                             |
| Reviewers            | <Name(s)>                                                                          |
| Status               | Draft / Reviewed / Published                                                       |

## Summary
<2-3 sentence executive summary.>

## Timeline
| Time (UTC)  | Event                                                                                       |
|-------------|---------------------------------------------------------------------------------------------|
| 00:00       | First symptom observed.                                                                     |
| 00:00       | Alert fired.                                                                                |
| ...         | ...                                                                                         |
| 00:00       | /status returned operational.                                                               |

## Impact
- Customer impact: <quantified>
- Data impact: <quantified>
- Regulatory impact: <yes/no — if yes, notification status>

## Root cause analysis
Five-why traversal from symptom to root cause. Cite file:line for any code change implicated.

## What went well
- ...

## What went poorly
- ...

## Action items
| ID  | Action                                  | Owner       | Due        | Status      |
|-----|-----------------------------------------|-------------|------------|-------------|
| AI-1| <change>                                | <name>      | YYYY-MM-DD | open/closed |

## Annex A — Incident timeline (raw)
<verbatim Slack / PagerDuty timeline>

## Annex B — Customer notification text
<exact text sent to customer; for Sev-0 only>
```

The postmortem is reviewed by the on-call who ran the incident, the incident commander, one engineer who was not on the rotation that week (fresh eyes), and Security Engineering. Action items are tracked to closure in the sprint board and reviewed at every retro.

### 6.1 Publication

For Sev-0 postmortems and any postmortem with customer-visible impact, the postmortem is published to `https://aegisagent.in/advisories/` with PII / customer-identifying detail redacted (the unredacted copy stays internal). The customer mentioned in the unredacted copy may request redactions before publication.

---

## 7. Drill cadence

Incident response is exercised against real signals every quarter:

| Drill                                       | Cadence       | Owner       |
|--------------------------------------------|---------------|-------------|
| Sev-0 audit-chain violation tabletop        | Quarterly     | Security-Eng |
| Sev-0 DR failover (per `docs/runbooks/dr.md`) | Quarterly   | SRE          |
| Sev-0 Merkle key compromise (`docs/runbooks/key_rotation.md`) | Annually | Security-Eng |
| Sev-1 backup restore (`docs/runbooks/restore_drill.md`) | Quarterly | SRE       |

Each drill produces a short report under `docs/operations/drills/YYYY-MM-DD-<name>.md` with the observed time-to-detect, time-to-recover, and any divergence from the runbook. Divergences become runbook updates.

---

## 8. Change log

| Version | Date       | Author        | Notes                                                                                                                                                          |
|---------|------------|---------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | SRE + Security Eng | First publication. Established Sev-0..3 classes, 72-hour customer-notify SLO for Sev-0 (aligns with GDPR Art. 33 and HIPAA § 164.410), 14-day postmortem SLA. Closes audit finding C5. |
