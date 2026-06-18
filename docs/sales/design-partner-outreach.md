# Design-Partner Outreach — Aegis v2.0 Case Studies

**Audience:** ByteHubble Sales + Founder + named design-partner leads.
**Owner:** Head of Customer Trust (Sales).
**Version:** 1.0 · 2026-06-18.
**Companion documents:**
- `SPRINT.md` §10 Track G — in-sprint commitment: 3 partners contacted, 1 verbal yes. Post-sprint 90d: 3 published case studies, 1 logo.
- `agies-bussiness.md` v1.3.0 — context briefing for talking points; lead with cryptographic transparency and the algorithm-downgrade defence.

This file is the playbook for the Track G outreach. It's not a marketing doc; it's the operator-readable spec for *which* design partners to ask, *what* to ask them for, and *how* to track the result.

---

## 1. Goal

By sprint close (Day 14): three named design-partner tenants identified, outreach email sent to each, **one verbal yes** for a case study secured.

Post-sprint (90 days): three redacted case studies published at `docs/case-studies/<slug>.md`, one public reference logo on `https://aegisagent.in`.

The Definition of Done in `SPRINT.md` §13 demands the in-sprint commitment only; the published artefacts depend on sales-cycle timing outside our control. We commit to the *outreach + first yes*.

---

## 2. Partner-selection criteria

The three partners are picked by Sales, not by Engineering. The criteria below are what Sales aligns on with the Founder before reaching out.

| # | Criterion                                                                     | Why                                                                                               |
|---|------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| 1 | Already a paying or pilot Aegis customer for ≥ 60 days.                       | They have a real story; "we plan to use Aegis" is not a case study.                                |
| 2 | Distinct vertical or use-case from the other two partners.                    | Avoids three identical case studies. Aim for one fintech / one DevOps / one healthcare or similar. |
| 3 | Decision-maker (CISO or VP-Eng) accessible by the Founder directly.            | Outreach via the sponsor's executive — not a forwarded inbound from the support inbox.             |
| 4 | At least one of: enterprise scale (≥ 500 employees), regulated industry, or named SOC2/PCI/HIPAA scope. | A small B-to-C SaaS reference doesn't help close Fortune-500 deals.                        |
| 5 | Friendly contractual posture: marketing-clause in their MSA already permits naming, or willingness to add it. | Avoids 60-day legal cycle for naming rights mid-case-study.                                 |

Compliance constraint: the partner's data, customer base, and traffic patterns are NOT named in the published case study. Redaction guidelines under §6.

---

## 3. Outreach email template

> **From:** Founder, ByteHubble  
> **To:** `<DECISION_MAKER_NAME>` <`<EMAIL>`>  
> **CC:** Head of Customer Trust  
> **Subject:** Quick ask — Aegis design-partner case study  
>
> Hi `<FIRST_NAME>`,
>
> Quick context: we're publishing the v2.0 GA milestone for Aegis at the end of this sprint. The release ships a formal STRIDE threat model, a SOC2 Type-II vendor engagement, and a public Merkle-chain transparency log that anyone can verify without an Aegis account. The full v1.3.0 context is in [agies-bussiness.md](https://github.com/Abhi-mishra998/aegis/blob/main/agies-bussiness.md) if useful.
>
> I'd like to ask: would `<COMPANY>` be open to being one of three named design partners on a published case study?
>
> **What we'd ask of you (≤ 3 hours of your team's time):**
> 1. A 30-minute call with me and your team lead to capture the story — what problem you brought to Aegis, what changed, what's still owed.
> 2. A short Q&A pass on the redacted draft (1–2 rounds, ≤ 1 hour combined).
> 3. Sign-off on the published version. You can mark anything as confidential and we'll redact.
>
> **What you get:**
> 1. Co-authored case study published at `aegisagent.in/customers/<your-slug>` (you control the slug and the framing).
> 2. The Aegis founder's personal commitment to be the named reviewer for any future Aegis-side change that touches your control plane.
> 3. A 25 % discount on the v2.0 GA renewal — applied at next contract anniversary.
> 4. Two reference calls per year handled by us, not you, with prospective customers you'd care about endorsing Aegis to.
>
> If this is interesting, can I send a 15-minute calendar? If not, no problem at all — happy to just send you the v2.0 release notes when they ship.
>
> Thanks for considering it,  
> `<FOUNDER_NAME>`

### 3.1 Variants

- **Verbal-yes follow-up.** Once the customer says yes verbally, follow up the same day with the formal opt-in (mutual marketing addendum to their MSA, draft below at §5).
- **Polite-no follow-up.** Thank them, add them to the "future case study" tag in the CRM, and ask one question: "is there anything we'd need to ship to make this a yes in six months?".
- **No-response after 14 days.** Founder follow-up on the same thread — single short message, no "just bumping this", just a one-line: "checking in once before I close this loop".

---

## 4. Tracker

Sales fills this table. Engineering reads it for context but does not edit.

| Slug          | Company             | Contact              | Vertical            | Pilot start | Outreach sent | First reply | Verbal yes | Draft delivered | Published | Logo on site |
|---------------|---------------------|----------------------|---------------------|-------------|---------------|-------------|------------|-----------------|-----------|--------------|
| `<slug-1>`    | `<COMPANY_1>`       | `<NAME>` `<EMAIL>`   | `<vertical>`        | YYYY-MM-DD  | YYYY-MM-DD    | YYYY-MM-DD  | yes/no     | YYYY-MM-DD      | YYYY-MM-DD| yes/no       |
| `<slug-2>`    | `<COMPANY_2>`       | `<NAME>` `<EMAIL>`   | `<vertical>`        | YYYY-MM-DD  | YYYY-MM-DD    | YYYY-MM-DD  | yes/no     | YYYY-MM-DD      | YYYY-MM-DD| yes/no       |
| `<slug-3>`    | `<COMPANY_3>`       | `<NAME>` `<EMAIL>`   | `<vertical>`        | YYYY-MM-DD  | YYYY-MM-DD    | YYYY-MM-DD  | yes/no     | YYYY-MM-DD      | YYYY-MM-DD| yes/no       |

Target: at least one row reaches column "Verbal yes" = yes by sprint close. Logo column flips post-sprint when contract amendment lands.

---

## 5. Marketing-rights addendum (template)

Attached to the customer's MSA as a one-page addendum. Legal reviews once; the same addendum is reused for all three partners.

> **Mutual marketing rights — `<DATE>`**
>
> 1. **Customer naming.** ByteHubble may name Customer ("`<COMPANY>`") in Aegis customer-facing materials (website, decks, public talks) as a customer of Aegis. The named reference includes only the company name and a logo; no usage volume, customer base, or revenue figure is named.
> 2. **Case study.** ByteHubble may publish one case study about Customer's use of Aegis at a URL of ByteHubble's choosing under `aegisagent.in`. Customer reviews and approves the published draft. Customer may withdraw the case study at any time on 30 days written notice; ByteHubble removes the URL from search and the website within 5 business days of the request.
> 3. **Reference calls.** Customer agrees to up to **two** reference calls per year with prospective ByteHubble customers, capped at 60 minutes each. The reference call agenda is shared with Customer at least 5 business days in advance. Customer may decline any individual call.
> 4. **Redaction.** Any specific dollar figure, employee count, traffic volume, customer count, or proprietary detail referenced in the case study is redacted unless Customer's CISO or General Counsel approves in writing on the published draft.
> 5. **Termination.** This addendum terminates on the later of (a) termination of the underlying MSA or (b) 60 days after Customer's withdrawal under §2 above.
> 6. **Consideration.** ByteHubble grants Customer a **25 % discount** on the v2.0 GA annual renewal, applied at the next contract anniversary following execution of this addendum.

`<LEGAL REVIEW PENDING>` — Legal counsel must verify the consideration clause (§6) does not trigger any rebate-disclosure requirement under Customer's procurement policy.

---

## 6. Redaction guidelines for case studies

The published case study at `docs/case-studies/<slug>.md` redacts on the following dimensions. Engineering must NOT volunteer any of these in the draft; Sales removes them on the customer's behalf before sending the draft to the customer.

| Class                            | Always redacted | Sometimes redacted               |
|----------------------------------|-----------------|----------------------------------|
| Specific monthly active agent count | yes          | n/a                              |
| Specific tenant_id                 | yes           | n/a                              |
| Specific employee names (other than the decision-maker who signed off) | yes | n/a                  |
| Tool names invoked by the customer's agents | no | yes if proprietary               |
| Customer-owned LLM prompts          | yes           | n/a                              |
| ARR, revenue, employee count        | yes           | only if customer explicitly publishes elsewhere |
| Compliance scope (e.g. "HIPAA-covered") | no        | yes if customer's audit posture is private |
| Aegis-issued policy bundles         | no            | yes if they encode customer process IP |

Use the placeholder `<REDACTED — <REASON>>` so the reader knows something was removed and why.

---

## 7. Published case-study template

Sales hands this skeleton to the Founder; the Founder runs the 30-minute interview and fills it in.

```markdown
# `<COMPANY>` — Aegis case study

**Industry:** `<vertical>`  
**Customer since:** `<YYYY-MM-DD>` (`<duration> pilot, currently at <plan> tier`)  
**Integration path:** Path A (SDK wrapper) / Path B (proxy) / Both.

## What `<COMPANY>` brought to Aegis

(2–3 paragraphs. The problem statement in the customer's voice.)

## What changed

(2–3 paragraphs. What Aegis catches, what's now possible, the controls in production.)

| Signal                                          | Action                |
|-------------------------------------------------|-----------------------|
| `<a specific Aegis signal they value>`          | `<deny / escalate>`   |
| `<...>`                                         | `<...>`               |

## What's still owed

(1–2 paragraphs. Honest gaps — items on Aegis's roadmap that matter to this customer.)

## Quote (customer's CISO / VP-Eng)

> "<short quote, ≤ 50 words>"
>
> — `<NAME>`, `<TITLE>`, `<COMPANY>`

## Verification

This case study has been reviewed and approved by `<COMPANY>` per the marketing-rights addendum signed on `<DATE>`.
```

---

## 8. Risks

| Risk                                                                                       | Likelihood | Mitigation                                                                                          |
|---------------------------------------------------------------------------------------------|-----------|------------------------------------------------------------------------------------------------------|
| All three target partners decline.                                                          | Low       | Have 5–7 names in reserve; pull from the renewal pipeline.                                          |
| Marketing addendum gets stuck in customer's legal for > 60 days.                            | Medium    | Use the addendum template above; do NOT customise per-customer unless their legal demands it.       |
| Customer says yes verbally, then ghosts on the draft.                                        | Medium    | Push gently for the 30-minute call within 7 days of verbal yes; that locks calendar momentum.        |
| Case study is published but the customer later asks for redaction of a specific claim.       | Low       | §5(4) above gives them a 30-day takedown right + ByteHubble has 5 business days to remove from search. |
| Confidentiality breach — a published case study contains something the customer didn't approve. | Low       | Two-person sign-off: Founder + customer's named contact + customer's General Counsel.              |

---

## 9. Change log

| Version | Date       | Author              | Notes                                                                                                                       |
|---------|------------|---------------------|-----------------------------------------------------------------------------------------------------------------------------|
| 1.0     | 2026-06-18 | Sales + Security Eng | First publication. Engineering-drafted scaffold; Sales executes the outreach + tracker. Closes Track G engineering side. |
