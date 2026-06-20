# Service Level Agreement — Template

**Audience:** Customer procurement / engineering counsel + ByteHubble legal.
**Status:** `<LEGAL REVIEW PENDING>` — engineering-drafted skeleton. Counsel must finalise the per-tier uptime commitments in §3 and the service-credit schedule in §5 against the executed Order Form pricing.
**Version:** 1.0 · 2026-06-20 (Sprint EI-8).
**Companion documents:**
- `docs/legal/msa-template.md` — Master Service Agreement that this SLA attaches to (§12.1 incorporates this document by reference).
- `docs/runbooks/disaster_recovery.md` — RTO / RPO operational targets that backstop §3.
- `docs/security/data_residency.md` — the region per-data-class residency table.

> **How to use this template.** Replace every `<CUSTOMER_LEGAL_ENTITY>`, `<EFFECTIVE_DATE>`, `<TIER>` placeholder. The two service tiers in §3 reflect the current Aegis tiers (Design-Partner and Enterprise); when a third tier is added, document it here in the same shape.

---

## 1. Parties

This Service Level Agreement (**"SLA"**) is between `<CUSTOMER_LEGAL_ENTITY>` (**"Customer"**) and ByteHubble Technologies Private Limited (**"ByteHubble"**). It is effective from `<EFFECTIVE_DATE>` and is incorporated by reference into the Master Service Agreement at `docs/legal/msa-template.md` (the **"MSA"**) and into every Order Form executed under the MSA.

## 2. Definitions

- **"Service"** has the meaning given in the MSA.
- **"Monthly Uptime Percentage"** means: `(Total Minutes in Month - Unavailable Minutes) / Total Minutes in Month × 100`, calculated per calendar month.
- **"Unavailable"** means the Service returns a `5xx` HTTP response, or fails to respond within 30 seconds, for more than two (2) consecutive 1-minute health-check probes against `https://aegisagent.in/health` (or the corresponding regional endpoint).
- **"Service Credit"** means a credit, calculated in §5, applied against future fees on the next invoice.
- **"Excluded Time"** has the meaning in §4.

## 3. Uptime commitments

| Tier | Monthly Uptime Percentage | Maximum Unavailable Minutes per month |
|---|---|---|
| **Design-Partner** (pilots, free trials, eu-west-1 design-partner phase) | **99.5%** | ≈ 217 minutes |
| **Enterprise** | **99.9%** | ≈ 43 minutes |

The applicable tier is set in the Order Form. The Design-Partner tier is offered during pilot and early-customer phases; the Enterprise tier requires a Multi-AZ RDS topology and the latest staggered-deploy posture and is priced accordingly.

ByteHubble measures Monthly Uptime Percentage from the public-internet probe at `s3://aegis-public-roots-628478946931/nightly/latest.json` (the same artefact the nightly verify workflow publishes per Sprint EI-4). For disputes, ByteHubble's internal Prometheus `up{job="gateway"}` rollup over the month is authoritative.

## 4. Excluded time

The following minutes do not count as Unavailable:

1. **Scheduled maintenance.** ByteHubble may schedule maintenance windows that take the Service offline for no more than four (4) hours per calendar month, with at least seventy-two (72) hours' written notice. Scheduled maintenance is announced via `<NOTIFICATION_EMAIL>` and posted to `https://aegisagent.in/.well-known/status`.
2. **Force majeure.** Any event covered by MSA §13.
3. **Customer-caused.** Unavailability resulting from Customer's actions, third-party software not provided by ByteHubble, or Customer's failure to comply with the MSA.
4. **Upstream LLM outages.** Unavailability of Anthropic or OpenAI APIs is excluded — Aegis returns `503 upstream_unavailable` which is a *correct* response, not an outage of Aegis itself.
5. **Customer-elected configuration.** A tenant explicitly placed in `degraded_mode_policy = block_all` for security reasons is not Unavailable; that is the policy outcome the Customer asked for.
6. **Sub-processor outages outside ByteHubble's control.** Unavailability of an AWS service in the chosen region for which AWS itself has issued a public outage notification is excluded for the duration AWS's outage notification covers.

## 5. Service credits

If Monthly Uptime Percentage falls below the commitment in §3, Customer is entitled to a Service Credit per the schedule below, applied automatically against the next invoice without Customer needing to file a claim. ByteHubble will publish a post-incident report within five (5) business days for any month that triggers a Service Credit.

| Monthly Uptime Percentage | Service Credit (% of monthly fees for the affected Order Form) |
|---|---|
| **Design-Partner tier (99.5% target)** | |
| 99.0% – 99.49% | 10% |
| 95.0% – 98.99% | 25% |
| < 95.0% | 50% |
| **Enterprise tier (99.9% target)** | |
| 99.5% – 99.89% | 10% |
| 95.0% – 99.49% | 25% |
| < 95.0% | 50% |

### 5.1 Maximum credit

Service Credits are the Customer's sole and exclusive remedy for a breach of this SLA and are capped at 50% of monthly fees for the affected Order Form for the affected month.

### 5.2 Right to terminate for chronic failure

If ByteHubble triggers a Service Credit in three (3) consecutive months in any twelve-month period, OR if Monthly Uptime Percentage falls below 95% in any single month, Customer may terminate the affected Order Form for cause on thirty (30) days' written notice with a pro-rata refund of pre-paid unused fees (notwithstanding MSA §5.2's general cure-period requirement).

## 6. Disaster recovery targets

These are operational targets that backstop the uptime commitment in §3; they are NOT separate SLA commitments, but a Customer-facing transparency into how ByteHubble plans against catastrophic loss:

| Target | Value | How verified |
|---|---|---|
| Recovery Time Objective (RTO) | < 1 hour for full data loss | Monthly DR drill — `docs/runbooks/dr_drill_log.md` |
| Recovery Point Objective (RPO) | < 24 hours | Daily automated RDS backups + on-demand snapshot before any destructive deploy |
| Multi-AZ database failover | < 60 seconds | RDS Multi-AZ; tested on every nightly chaos run that kills the active AZ replica |
| Cross-region snapshot copy | Daily | Operational only — not currently mirrored cross-region; on roadmap |

## 7. Support response targets

The following are response-time *commitments* (first human reply within the stated window). They do NOT trigger Service Credits — they are separately measured and reported quarterly.

| Severity | Definition | Enterprise tier first-response | Design-Partner tier first-response |
|---|---|---|---|
| **Sev-0** (production down, all tenants) | `/health` returns non-200 for > 5 min OR all customer dashboards are unreachable | 15 minutes, 24×7 | 1 hour, 9×5 business hours |
| **Sev-1** (single-tenant impact) | Customer's tenant cannot execute, OR a critical security control (RBAC, isolation) has failed | 1 hour, 24×7 | 4 hours, 9×5 |
| **Sev-2** (degraded) | Documented feature returns incorrect result; workaround available | 4 hours, 9×5 | Next business day |
| **Sev-3** (request) | Documentation question, feature request, non-production curiosity | Next business day | Within 5 business days |

## 8. Reporting

ByteHubble publishes a Monthly Uptime Percentage rollup to `s3://aegis-public-roots-628478946931/uptime/<YYYY-MM>.json` within five (5) business days of month-end. Customer may also query the real-time `https://aegisagent.in/status` endpoint at any time.

## 9. Modifications

This SLA may be updated by ByteHubble; material reductions in the uptime commitment require ninety (90) days' prior written notice and give Customer the right to terminate the affected Order Form for cause if Customer does not consent.

---

*End of SLA Template v1.0 · 2026-06-20. Engineering-drafted; legal counsel must finalise before counter-signature.*
