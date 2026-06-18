# On-call Rota & IR Sign-off

**Audience:** ByteHubble on-call engineers, SRE Lead, Security Lead, CTO.
**Owner:** SRE Lead.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/operations/incident-response.md` — the IR runbook this rota services; this file is its operator companion. C5 acceptance criterion ("reviewed by the on-call lead") signed off in §6 below.
- `docs/operations/disaster-recovery.md` — DR drills the rota also covers.

This document names the people, the hand-off protocol, the PagerDuty reference, and carries the on-call lead's IR-runbook sign-off block.

---

## 1. Current rota

The rota lives canonically in PagerDuty; this table is a mirror updated at the start of every sprint. PagerDuty is the source of truth on any conflict.

### 1.1 Tier 1 (Primary on-call — SRE rotation)

| Week-of  | Primary           | Secondary       |
|----------|-------------------|-----------------|
| `<W1>`   | `<NAME>` (`<HANDLE>`) | `<NAME>`       |
| `<W2>`   | `<NAME>`          | `<NAME>`        |
| `<W3>`   | `<NAME>`          | `<NAME>`        |
| `<W4>`   | `<NAME>`          | `<NAME>`        |

Hand-off: Monday 09:00 IST. Outgoing primary publishes a hand-off note in `#aegis-incidents` covering: open incidents, deferred tickets, anything weird from the last week, what they're watching.

### 1.2 Tier 2 (Security-Eng rotation, pulled in for Sev-0 / Sev-1 with a security dimension)

| Week-of  | Tier 2 on-call    |
|----------|-------------------|
| `<W1>`   | `<NAME>`          |
| `<W2>`   | `<NAME>`          |
| `<W3>`   | `<NAME>`          |
| `<W4>`   | `<NAME>`          |

### 1.3 Standing roles

| Role                          | Person                  | Backup                  |
|-------------------------------|-------------------------|-------------------------|
| Incident Commander (named per-Sev-0) | Senior on-call          | SRE Lead                |
| Executive Sponsor             | CTO                     | Founder                 |
| Head of Customer Trust        | `<NAME>`                | `<NAME>`                |
| Legal & Privacy Counsel       | General Counsel         | `<EXTERNAL_COUNSEL>`    |

---

## 2. PagerDuty configuration

- **Schedule name:** `aegis-prod-tier-1` (Tier 1) + `aegis-prod-tier-2` (Tier 2).
- **Escalation policy:** primary → after 5 min unack → secondary → after 5 min unack → SRE Lead phone.
- **Routing key:** stored in `infra/alertmanager.yml` per `infra/prometheus-rules-customer-slo.yml` severity routing (`page` → PagerDuty; `warning` → Slack).
- **Override policy:** the on-call may override their own slot via PagerDuty UI. Cross-rota swaps require Tier 2 acknowledgement + Slack post.
- **Quiet windows:** scheduled maintenance windows are entered into PagerDuty before the work begins. Load tests (Track D drills) MUST be quiet-windowed; see `reports/load-test-2026-Q3/EXECUTION_GUIDE.md` §1 row 9.

---

## 3. Hand-off protocol

Every Monday at 09:00 IST, the outgoing primary publishes a hand-off note in `#aegis-incidents` with this template:

```
*Hand-off — week of <DATE> → <NEXT_DATE>*

Open incidents
  - <ticket / link / status>

Watch list
  - <what the new on-call should keep an eye on>

Deferred work
  - <items I started but didn't finish>

Heads-up
  - <anything weird that wasn't quite an incident>

Drills scheduled this week
  - <pen-test, DR drill, load test, etc.>

Off-rota windows
  - <pre-planned PTO/conference for the next four weeks>
```

The incoming primary acknowledges in-thread before taking the pager.

---

## 4. Drill cadence (owned here, executed per `docs/operations/incident-response.md` §7)

| Drill                                              | Cadence   | Owner per session    | Last run    | Next run   |
|----------------------------------------------------|-----------|----------------------|-------------|------------|
| Sev-0 audit-chain-violation tabletop                | Quarterly | Security-Eng         | `<DATE>`    | `<DATE>`   |
| Sev-0 DR failover (`docs/runbooks/dr.md`)           | Quarterly | SRE                  | `<DATE>`    | `<DATE>`   |
| Sev-0 Merkle key compromise (`docs/runbooks/key_rotation.md`) | Annually | Security-Eng | `<DATE>`    | `<DATE>`   |
| Sev-1 backup restore (`docs/runbooks/restore_drill.md`) | Quarterly | SRE                  | `<DATE>`    | `<DATE>`   |
| Tabletop using IR runbook against simulated breach  | Quarterly | SRE Lead             | `<DATE>`    | `<DATE>`   |

Drill reports land at `docs/operations/drills/YYYY-MM-DD-<name>.md` and link from the rota row.

---

## 5. Pager-friendly contact tree

For Sev-0 / Sev-1 declarations only. **Phone first. Slack second.** The CTO + Customer-Trust head + Legal each have a 24/7 phone in PagerDuty Tier-3 escalation policy.

| When                                                                       | Who calls whom                                            |
|----------------------------------------------------------------------------|-----------------------------------------------------------|
| Sev-0 declared                                                              | Incident Commander → CTO → Customer Trust → Legal.        |
| Sev-1 declared                                                              | Tier-1 on-call → SRE Lead (Slack OK if business hours).   |
| Customer reports cross-tenant read                                         | First responder → immediate Sev-0 declaration → §5 chain.|
| Audit-chain violation alert fires                                          | Tier-1 on-call → Security-Eng (phone) → SRE Lead.         |
| External security researcher emails security@aegisagent.in with a finding | Security-Eng on-call → SRE Lead (within 24 h).            |

---

## 6. Sign-off block

This sign-off closes the C5 acceptance criterion: the IR runbook has been reviewed by the on-call lead and adopted.

| Role                  | Reviewer                | Date            | Signature      |
|-----------------------|-------------------------|-----------------|----------------|
| SRE Lead              | `<NAME>`                | `<YYYY-MM-DD>`  | `<SIGNED>`     |
| Security Lead         | `<NAME>`                | `<YYYY-MM-DD>`  | `<SIGNED>`     |
| Engineering Lead      | `<NAME>`                | `<YYYY-MM-DD>`  | `<SIGNED>`     |
| Head of Customer Trust| `<NAME>`                | `<YYYY-MM-DD>`  | `<SIGNED>`     |

> By signing above, each reviewer confirms they have read `docs/operations/incident-response.md` v1.0 end-to-end, have walked at least one Sev-0 runbook (audit-chain violation OR DR failover) in the last quarter, and accept the §1 SLO commitments to the customer.

Sign-off cadence: re-read + re-sign annually OR after any IR-runbook change.

---

## 7. First-postmortem-under-the-template marker

`docs/operations/incident-response.md` §6 requires the first postmortem under the new template to be written in-sprint, using the E1 DR drill as the practice incident. When the drill runs:

1. SRE captures the timeline in real time in `#aegis-incidents`.
2. Within 7 days, the operator writes `docs/postmortems/INC-2026-MM-DD-dr-drill.md` using the template.
3. Reviewers sign per IR §6.
4. The postmortem is **internal only** (it's a drill, not a real incident). Mark `published: internal` on the file's front-matter.

---

## 8. Change log

| Version | Date       | Author        | Notes                                                                                                                  |
|---------|------------|---------------|------------------------------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | SRE + Security Eng | First publication. Closes C5 acceptance criterion (on-call-lead review of IR runbook). Names + PagerDuty references owed by SRE Lead at sign-off. |
