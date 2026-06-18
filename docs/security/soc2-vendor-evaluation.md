# SOC2 Type II Vendor Evaluation

**Audience:** ByteHubble Compliance + Engineering leadership.
**Status:** `<COMPLIANCE REVIEW PENDING>` — engineering-drafted evaluation framework. Compliance owns the final decision; this scaffold gives them the matrix.
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `docs/security/soc2_tracker.md` — gets updated to `ENGAGED — <Vendor>, kickoff <date>` once selection lands.
- `SPRINT.md` §9 F1 — the sprint commitment is engagement-letter signed by sprint close; report delivery is 3-6 months after kickoff.

---

## 1. Goal

Select one SOC2 Type II compliance-automation vendor for ByteHubble by sprint close. Selection criteria below; the engagement letter signature is the gating event for SPRINT.md §13 "Compliance" row.

This is an audit-automation vendor, not the SOC2 auditor itself. The vendor manages controls evidence + monitors continuous compliance; the auditor (likely a CPA firm — Schellman, BARR, Insight Assurance, or similar) is selected separately.

---

## 2. Shortlist

Three vendors evaluated. All three are credible at our scale (Series A through B SaaS, US + EU customers). Tier-1 enterprise vendors (Tugboat Logic, OneTrust) are out of scope — overkill for our stage and budget.

| Vendor      | URL                       | Founded | Approx ARR | Why on the shortlist                                     |
|-------------|---------------------------|---------|------------|----------------------------------------------------------|
| Drata       | https://drata.com         | 2020    | ~$100M+    | Strongest SOC2 + ISO 27001 dual-track. Heavy AWS focus matches our reference deployment. |
| Vanta       | https://vanta.com         | 2018    | ~$200M+    | Largest market share. Most controls library entries. Broadest auditor-firm relationships. |
| Thoropass   | https://thoropass.com     | 2019    | ~$30M      | Tighter ICP at our scale (Series A–B). Audit firm baked into pricing — one-stop. Less "enterprise-feature bloat". |

---

## 3. Selection criteria (weighted)

Compliance scores each vendor against the seven dimensions below. Higher weight = more decisive for our use case. Engineering provides the technical inputs; Compliance applies the weights and signs off.

| # | Dimension                                          | Weight | Rationale for the weight                                                                              |
|---|----------------------------------------------------|-------:|-------------------------------------------------------------------------------------------------------|
| 1 | SOC2 control-library coverage (% Aegis-relevant)   | 25 %   | Missing controls = manual evidence work, which erodes the automation value-prop.                       |
| 2 | Evidence-collection automation for AWS + Postgres + Clerk + Slack | 20 %   | Our stack is exactly these. Native integrations beat manual screenshots.       |
| 3 | Total 12-month price (subscription + audit fees)   | 15 %   | Founder-stage budget. Pricing matters but is not the headline.                                          |
| 4 | Audit-firm relationships (named firms + intro-friction) | 15 %   | A vendor that ships a vetted auditor saves 4-6 weeks of CPA shopping.                              |
| 5 | Customer references at our scale (Series A–B SaaS, security tooling) | 10 %   | Tier-1 references are noise; we need to talk to companies that look like us.            |
| 6 | Time-to-Type-I attestation                         | 10 %   | We want a defensible "SOC2 Type I in flight" claim before Q4 GTM cycle.                                 |
| 7 | Aegis-specific must-haves (custom-control authoring, Merkle-chain evidence ingest) | 5 %    | Most vendors don't natively understand "cryptographic evidence chain"; we may need custom controls. |

Total = 100 %.

---

## 4. Comparison matrix

Engineering fills the technical columns; Compliance fills the qualitative ones during vendor calls.

| Dimension                                  | Drata           | Vanta           | Thoropass       | Notes                                                                       |
|--------------------------------------------|-----------------|-----------------|-----------------|-----------------------------------------------------------------------------|
| Controls coverage (SOC2 CC1–CC8)           | `<%>`           | `<%>`           | `<%>`           | Score = mapped controls / SOC2 mandatory controls.                          |
| AWS evidence (CloudTrail, IAM, KMS, ALB)   | ✅ native       | ✅ native       | ✅ native       | All three. Differentiation is the depth, not presence.                       |
| Postgres evidence (audit, RDS encryption)  | `<yes/no>`      | `<yes/no>`      | `<yes/no>`      | Drata strongest on RDS encryption-at-rest evidence per vendor docs.          |
| Clerk evidence                              | `<yes/no>`      | `<yes/no>`      | `<yes/no>`      | Clerk has docs at https://clerk.com/docs/security/soc2; check integration listing. |
| Slack evidence                              | ✅              | ✅              | `<yes/no>`      |                                                                              |
| GitHub evidence (branch-protection, MFA)   | ✅              | ✅              | `<yes/no>`      |                                                                              |
| Custom-control authoring                   | `<yes/no>`      | `<yes/no>`      | `<yes/no>`      | We need this for the Merkle-chain control and the append-only trigger control. |
| Continuous monitoring (drift alerts)       | ✅              | ✅              | `<yes/no>`      |                                                                              |
| Time-to-Type-I                             | ~6 weeks        | ~8 weeks        | ~6 weeks        | Per vendor docs; verify with each on the call.                              |
| Subscription price (estimate, 12-month)    | `<$>`           | `<$>`           | `<$>`           | Get sticker price and negotiated rate on the call.                          |
| Audit-firm included?                       | No (intro)      | No (intro)      | Yes (bundled)   | Thoropass bundles the auditor; Drata + Vanta refer.                         |
| Audit-firm relationships                   | `<list>`        | `<list>`        | bundled         | Get the list of named firms from each.                                      |
| Customer references at our scale           | `<3 names>`     | `<3 names>`     | `<3 names>`     | Ask for 3 reference customers, Series A–B SaaS, security-adjacent.          |
| Pricing transparency                       | published       | call-only       | published       |                                                                              |
| Migration friction if we switch later      | medium          | medium          | medium          | Controls evidence is portable; subscription contracts are not.              |
| Aegis-specific concerns                    | Merkle-chain custom control may need a Compliance Engineer; verify the chain-violation alert can be wired as a continuous-monitoring signal. | Same as Drata. | Same; bundled auditor may have less depth on cryptographic controls — confirm. | Probe each on whether their evidence-ingest API can receive a JSON payload from the chain-violation alert. |

---

## 5. Decision rubric

Compliance fills the scored matrix on call-completion. A vendor wins when:

1. **Hard floor:** SOC2 control-library coverage ≥ 95 % AND native integrations for AWS + Postgres + Clerk + Slack + GitHub.
2. **Soft tiebreaker:** Total 12-month cost (subscription + audit fees + Aegis engineering integration time) is within 15 % of the cheapest qualifying vendor.
3. **Final tiebreaker:** Customer references at our scale unanimously positive; audit-firm relationships acceptable.

Document the score per criterion + the final tally in §6.

---

## 6. Recommendation (to be completed post-call)

`<COMPLIANCE FILLS POST-CALL>`

Recommended vendor: `<NAME>`.
Total score: `<X / 100>`.

Reasoning (3-5 bullets):
- `<bullet>`
- `<bullet>`

Dissenting view: `<NAME>` is the runner-up because `<reason>`. We did not select it because `<reason>`.

---

## 7. Next steps after selection

1. Counter-sign the engagement letter.
2. Update `docs/security/soc2_tracker.md`: status → `ENGAGED — <vendor>, kickoff <date>`.
3. Schedule kickoff call within 14 days of engagement.
4. Engineering owner: `<NAME>` — manages integration credentials, custom-control authoring, evidence-ingest pipeline.
5. Compliance owner: `<NAME>` — owns the auditor relationship, controls documentation, employee-training rollout.
6. Target Type-I attestation date: `<DATE>` (6 weeks post-kickoff).
7. Target Type-II observation window start: `<DATE>` (6 months observation = first report Q1 2027).

---

## 8. Risks and unknowns

| Risk                                                                                          | Likelihood | Mitigation                                                          |
|------------------------------------------------------------------------------------------------|-----------|---------------------------------------------------------------------|
| Selected vendor cannot model the Merkle-chain control as a custom control.                     | Medium    | Probe pre-contract. If absent, treat as a Type-II observation gap and document separately. |
| Vendor's recommended auditor declines the engagement (small audit firms are selective).        | Low       | Have a second-choice auditor ready before counter-signature.        |
| Subscription contract has unfavourable termination terms.                                       | Low       | Legal review the engagement letter before counter-signature.        |
| Evidence-collection API cannot pull from our internal Prometheus alerts.                       | Low       | Most vendors accept webhook-based custom evidence; build the bridge ourselves if needed. |
| Auditor opinion on the cryptographic transparency chain is "interesting but irrelevant to SOC2 controls". | Medium | The chain is part of CC7 (security operations); document it explicitly in the controls narrative. |

---

## 9. Change log

| Version | Date       | Author        | Notes                                                                                            |
|---------|------------|---------------|--------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Security Eng  | First publication. Engineering-drafted scaffold; Compliance to complete §4 + §6 after vendor calls. Closes engineering side of Track F1. |
