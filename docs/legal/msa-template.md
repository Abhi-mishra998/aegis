# Master Service Agreement — Template

**Audience:** Customer procurement / commercial counsel + ByteHubble legal.
**Status:** `<LEGAL REVIEW PENDING>` — engineering-drafted skeleton. Counsel must finalise §10 (Limitation of Liability cap), §15 (Governing Law / venue), and any per-Order-Form addenda before counter-signature.
**Version:** 1.0 · 2026-06-20 (Sprint EI-8).
**Companion documents:**
- `docs/legal/dpa-template.md` — Data Processing Agreement (GDPR Art. 28, India DPDP Act §8).
- `docs/legal/baa-template.md` — Business Associate Agreement (HIPAA-Covered-Entity overlay).
- `docs/legal/sla-template.md` — Service Level Agreement.
- `docs/security/subprocessors.md` — current sub-processor register (incorporated by reference).
- `docs/security/data_residency.md` — region-of-record table (referenced in §3 + §12).

> **How to use this template.** Replace every `<CUSTOMER_LEGAL_ENTITY>`, `<CUSTOMER_REGISTERED_ADDRESS>`, `<EFFECTIVE_DATE>`, `<JURISDICTION>`, `<ORDER_FORM_REF>`, `<INITIAL_TERM_MONTHS>`, and `<NOTIFICATION_EMAIL>` placeholder. The Order Form referenced in §3 carries the per-engagement specifics (price, seats, region, support tier); this MSA is the master legal framework that every Order Form attaches to.

---

## 1. Parties

This Master Service Agreement (**"MSA"**) is entered into between:

- **Customer:** `<CUSTOMER_LEGAL_ENTITY>`, having its registered office at `<CUSTOMER_REGISTERED_ADDRESS>` (**"Customer"**).
- **Provider:** ByteHubble Technologies Private Limited, having its registered office at `<BYTEHUBBLE_REGISTERED_ADDRESS>` (**"ByteHubble"**, providing the Aegis platform).

Each a **"Party"** and together the **"Parties"**. This MSA is effective from `<EFFECTIVE_DATE>` (the **"Effective Date"**).

## 2. Definitions

Capitalised terms used but not otherwise defined herein have the meanings given in the Order Form, the DPA, the BAA (if applicable), or the SLA, each as incorporated by reference into this MSA. Without limitation:

- **"Aegis"** means the AI agent governance platform operated by ByteHubble at `aegisagent.in` (and per-region instances such as `eu.aegisagent.in`), including the runtime decision engine, audit chain, transparency log, and management console.
- **"Customer Data"** means all data, content, or information that Customer or its Authorised Users submit to or have generated for them by the Services.
- **"Documentation"** means the user-facing technical documentation published at `https://aegisagent.in/docs/` and updated from time to time.
- **"Order Form"** means an ordering document executed by both Parties that references this MSA and specifies the Services, term, fees, and quantities for a given engagement.
- **"Services"** means access to and use of Aegis as specified in an Order Form.

## 3. Services

ByteHubble will provide Customer access to the Services as described in each Order Form (`<ORDER_FORM_REF>`). The Services are delivered as software-as-a-service from the AWS region(s) selected by Customer in the applicable Order Form. The mapping of Aegis components to AWS regions and the data-residency commitments per data class are described in `docs/security/data_residency.md`, incorporated by reference.

Customer may make the Services available to its employees, contractors, and agents who have been provisioned an Aegis account by Customer (**"Authorised Users"**). Customer is responsible for Authorised Users' compliance with this MSA.

ByteHubble reserves the right to enhance or modify the Services so long as no such change materially degrades the functionality or security posture of the Services. Material changes that reduce functionality or weaken security will be notified to Customer at least thirty (30) days in advance.

## 4. Fees and Payment

Customer will pay the fees specified in each Order Form. Unless otherwise stated:

- Fees are invoiced annually in advance and are due within thirty (30) days of invoice.
- Fees are exclusive of all taxes, levies, or duties imposed by taxing authorities; Customer is responsible for these.
- Late payments accrue interest at 1.5% per month (or the maximum allowed by law if lower) from the due date.
- All fees are non-refundable except as expressly stated in §5 (Termination), §9 (Warranties), or the SLA service-credit schedule.
- ByteHubble may suspend access to the Services on thirty (30) days' written notice of non-payment.

## 5. Term and Termination

5.1 **Term.** The initial term of this MSA is `<INITIAL_TERM_MONTHS>` months from the Effective Date and will auto-renew for successive twelve (12) month terms unless either Party gives written notice of non-renewal at least sixty (60) days before the end of the then-current term.

5.2 **Termination for cause.** Either Party may terminate this MSA on thirty (30) days' written notice if the other Party materially breaches the MSA and fails to cure within that period. ByteHubble may terminate immediately on written notice if Customer's use of the Services breaches §6 (Use Restrictions) and the breach is incapable of cure or creates an immediate risk to other ByteHubble customers.

5.3 **Termination for insolvency.** Either Party may terminate immediately if the other Party becomes insolvent, files for bankruptcy protection, or has a receiver appointed.

5.4 **Effect of termination.** Upon termination Customer's right to access the Services ends. ByteHubble will, on written request received within thirty (30) days of the termination date, provide Customer with a one-time export of Customer Data via the `/v1/audit/export` and `/scim/v2/Users` SCIM endpoints. After ninety (90) days from termination ByteHubble may permanently delete Customer Data per the retention policy in `docs/security/data_retention.md` and §11 of the DPA. Cryptographic audit-chain hashes published to the public transparency log before termination remain published — they contain no Customer plaintext.

5.5 **Survival.** Sections 6 (Use Restrictions), 7 (IP), 8 (Confidentiality), 10 (Liability), 11 (Indemnification), 15 (Governing Law), and any other terms that by their nature should survive will survive termination.

## 6. Use Restrictions

Customer will not, and will not permit any Authorised User to:

- Reverse-engineer, decompile, or attempt to derive the source code of the Services;
- Resell, sublicense, or lease the Services to a third party except as an integrated component of Customer's own product offering;
- Use the Services to develop a directly competing product;
- Bypass the Aegis tenant-isolation, RBAC, or rate-limit controls described in `docs/security/rbac_matrix.md`;
- Use the Services to process content that violates applicable law (illegal content, CSAM, terrorism financing, sanctions-targeted parties);
- Conduct a security assessment, penetration test, or load test against the production environment without ByteHubble's prior written consent (use the demo workspace at `/demo/spawn-workspace` or contract for an isolated staging tenant);
- Knowingly upload malicious code, viruses, or any payload designed to disrupt the Services or other customers' tenants.

## 7. Intellectual Property

7.1 **ByteHubble IP.** As between the Parties, ByteHubble retains all right, title, and interest in and to the Services, the Documentation, the Aegis platform, and any modifications or improvements thereto. No rights are granted to Customer other than the limited, non-exclusive, non-transferable right to access and use the Services during the term in accordance with this MSA.

7.2 **Customer Data.** Customer retains all right, title, and interest in and to Customer Data. Customer grants ByteHubble a worldwide, non-exclusive, royalty-free licence to process Customer Data solely as necessary to provide the Services and to comply with this MSA, the DPA, and applicable law.

7.3 **Feedback.** If Customer provides ByteHubble with suggestions, comments, or feedback on the Services, ByteHubble may use that feedback without restriction or obligation to Customer.

7.4 **Aggregated and de-identified data.** ByteHubble may use Customer Data on an aggregated, anonymised, and de-identified basis to improve the Services, generate benchmarks, and create industry reports, provided that such aggregated data does not identify Customer or any individual.

## 8. Confidentiality

Each Party (the **"Receiving Party"**) will protect the Confidential Information of the other (the **"Disclosing Party"**) with the same degree of care it uses to protect its own confidential information, and in no event less than reasonable care. The Receiving Party will use Confidential Information only as necessary to exercise its rights or perform its obligations under this MSA. Customer Data is Customer's Confidential Information. The Services, Aegis architecture (other than the publicly-published technical documentation), pricing, and product roadmap are ByteHubble's Confidential Information.

The Receiving Party may disclose Confidential Information if compelled by law, provided that (where lawful) it gives the Disclosing Party prompt notice and reasonable assistance to seek a protective order.

The confidentiality obligations survive termination of this MSA for a period of three (3) years, except for trade secrets which remain protected for so long as they qualify as trade secrets under applicable law.

## 9. Warranties and Disclaimer

9.1 **Mutual.** Each Party warrants that it has the right, power, and authority to enter into and perform this MSA.

9.2 **ByteHubble service warranty.** ByteHubble warrants that during the term the Services will perform substantially in accordance with the Documentation and the SLA. Customer's sole and exclusive remedy for a breach of this warranty is the service-credit schedule in the SLA, or, if ByteHubble is unable to restore the Services to the warranted standard within sixty (60) days, termination of the affected Order Form with a pro-rata refund of pre-paid unused fees.

9.3 **Disclaimer.** EXCEPT AS EXPRESSLY SET FORTH IN THIS §9, THE SERVICES ARE PROVIDED **"AS IS"** AND BYTEHUBBLE DISCLAIMS ALL OTHER WARRANTIES, EXPRESS OR IMPLIED, INCLUDING ANY WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, OR NON-INFRINGEMENT. AEGIS IS A GOVERNANCE LAYER THAT EVALUATES AI-AGENT ACTIONS AGAINST POLICY; IT DOES NOT GUARANTEE THAT EVERY UNSAFE ACTION WILL BE BLOCKED OR THAT EVERY SAFE ACTION WILL BE ALLOWED. CUSTOMER ACKNOWLEDGES THE PROBABILISTIC NATURE OF AI MODELS AND ACCEPTS RESIDUAL RISK.

## 10. Limitation of Liability

10.1 **Cap.** EXCEPT FOR EXCLUDED LIABILITIES (DEFINED BELOW), EACH PARTY'S TOTAL CUMULATIVE LIABILITY ARISING OUT OF OR RELATING TO THIS MSA WILL NOT EXCEED THE TOTAL FEES PAID OR PAYABLE BY CUSTOMER TO BYTEHUBBLE UNDER THE APPLICABLE ORDER FORM IN THE TWELVE (12) MONTHS IMMEDIATELY PRECEDING THE EVENT GIVING RISE TO LIABILITY.

10.2 **Excluded damages.** IN NO EVENT WILL EITHER PARTY BE LIABLE FOR ANY INDIRECT, INCIDENTAL, CONSEQUENTIAL, SPECIAL, EXEMPLARY, OR PUNITIVE DAMAGES, INCLUDING LOST PROFITS, LOST REVENUE, OR LOST DATA, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.

10.3 **Excluded liabilities.** The limitations in §10.1 and §10.2 do not apply to: (a) a Party's indemnification obligations under §11; (b) Customer's payment obligations under §4; (c) breach by either Party of its confidentiality obligations under §8; (d) ByteHubble's breach of its data-protection obligations under the DPA (subject to a separate cap equal to twenty-four (24) months of fees); or (e) liability that cannot be limited by applicable law.

## 11. Indemnification

11.1 **ByteHubble indemnity.** ByteHubble will defend Customer from any third-party claim that the Services as provided by ByteHubble infringe a third party's intellectual-property right enforceable in the jurisdiction where the Services are delivered, and will indemnify Customer against amounts awarded against Customer (including reasonable attorney's fees) in any such claim. If the Services become or are likely to become the subject of an infringement claim, ByteHubble may at its option: (i) modify the Services to make them non-infringing; (ii) procure a licence for Customer's continued use; or (iii) terminate the affected Order Form and refund pre-paid unused fees.

This indemnity does not apply to claims arising from (a) Customer Data, (b) modifications to the Services not authorised by ByteHubble, (c) combination of the Services with third-party software not provided by ByteHubble, or (d) Customer's use of the Services other than in accordance with this MSA or the Documentation.

11.2 **Customer indemnity.** Customer will defend ByteHubble from any third-party claim arising out of Customer Data, Customer's use of the Services in violation of §6, or Customer's breach of applicable law, and will indemnify ByteHubble against amounts awarded against ByteHubble in any such claim.

11.3 **Procedure.** The indemnified Party will promptly notify the indemnifying Party of the claim, give the indemnifying Party sole control of the defence and settlement (subject to the indemnified Party's right to participate at its own cost), and reasonably co-operate with the defence. The indemnifying Party will not settle any claim in a way that imposes a material liability or admission on the indemnified Party without prior written consent (not unreasonably withheld).

## 12. Compliance, Privacy, and Security

12.1 **Incorporated agreements.** The DPA at `docs/legal/dpa-template.md` and (if Customer is a HIPAA Covered Entity) the BAA at `docs/legal/baa-template.md` are incorporated by reference into this MSA and form part of the Parties' agreement. In the event of conflict between this MSA and the DPA/BAA on a privacy or security topic, the DPA/BAA controls.

12.2 **Sub-processors.** ByteHubble's sub-processors are listed at `docs/security/subprocessors.md`. ByteHubble will give Customer thirty (30) days' written notice of any new sub-processor that gains access to a previously-unshared data class. Customer may terminate the affected Order Form for cause if it has a reasonable, documented objection to a new sub-processor and ByteHubble cannot offer a commercially reasonable alternative.

12.3 **Data residency.** Customer's tenant data resides in the AWS region specified in the Order Form. The per-data-class residency table is at `docs/security/data_residency.md`. Tenant runtime data never crosses regions; static artifacts (deploy bundle, UI assets) may cross because they contain no personal data.

12.4 **Security controls.** ByteHubble maintains the security controls described in `docs/security/data_classification.md`, `docs/security/rbac_matrix.md`, and `docs/security/shared_responsibility.md` for the duration of the term and will not materially weaken them.

12.5 **Audits.** Customer may, no more than once per twelve-month period and on at least thirty (30) days' prior written notice, request ByteHubble's then-current SOC 2 report (or equivalent independent attestation, when available) and the latest customer security package generated by `scripts/ops/build_customer_security_package.sh`. ByteHubble will provide these under reasonable confidentiality terms.

## 13. Force Majeure

Neither Party will be liable for delay or failure to perform (other than payment obligations) caused by events beyond its reasonable control, including acts of war, terrorism, natural disasters, government action, internet or upstream-provider failures, or pandemic. The affected Party will give prompt notice and use commercially reasonable efforts to resume performance. If a force majeure event continues for more than ninety (90) consecutive days, either Party may terminate the affected Order Form on written notice with a pro-rata refund of pre-paid unused fees.

## 14. Notices

All notices must be in writing and delivered to the addresses set out in §1 (or to the email address `<NOTIFICATION_EMAIL>` for routine commercial notices). Notices are deemed received: (a) on personal delivery; (b) one business day after deposit with a recognised overnight courier; (c) three business days after deposit in the post (postage prepaid); or (d) on the next business day after email transmission with confirmation of receipt.

## 15. Governing Law and Dispute Resolution

15.1 **Governing law.** This MSA is governed by the laws of `<JURISDICTION>`, without regard to its conflict-of-laws principles.

15.2 **Disputes.** Any dispute arising out of or relating to this MSA will first be the subject of good-faith negotiations between the Parties' designated senior executives for a period of thirty (30) days. If unresolved, the dispute will be submitted to binding arbitration administered under the rules of `<ARBITRAL_INSTITUTION>` and conducted in `<ARBITRATION_SEAT>` by a single arbitrator in the English language. Either Party may seek interim injunctive relief in any court of competent jurisdiction to protect intellectual-property or confidential information.

## 16. General

16.1 **Entire agreement.** This MSA (together with the DPA, BAA if applicable, the SLA, every executed Order Form, and the documents incorporated by reference herein) constitutes the entire agreement between the Parties on its subject matter and supersedes all prior or contemporaneous proposals, agreements, or communications.

16.2 **Order of precedence.** If there is a conflict among the constituent documents, the order of precedence is: (i) the executed Order Form; (ii) the DPA / BAA on privacy or security topics; (iii) the SLA on availability topics; (iv) this MSA.

16.3 **Amendments.** Amendments to this MSA require a written instrument signed by authorised representatives of both Parties.

16.4 **Assignment.** Neither Party may assign this MSA without the other Party's prior written consent (not unreasonably withheld), except that either Party may assign without consent to a successor in connection with a merger, acquisition, or sale of substantially all assets.

16.5 **Independent contractors.** The Parties are independent contractors. This MSA does not create any agency, partnership, joint venture, or employment relationship.

16.6 **No waiver.** Failure to enforce any provision is not a waiver of the right to enforce it later.

16.7 **Severability.** If any provision is held unenforceable, the remaining provisions will continue in full force and the unenforceable provision will be reformed to the minimum extent necessary to make it enforceable while preserving the Parties' original intent.

16.8 **Counterparts.** This MSA may be executed in counterparts (including electronically), each of which is deemed an original and all of which together constitute one instrument.

---

**IN WITNESS WHEREOF**, the Parties have executed this MSA effective as of the Effective Date.

| For Customer | For ByteHubble |
|---|---|
| `<CUSTOMER_LEGAL_ENTITY>` | ByteHubble Technologies Private Limited |
| Signature: ____________________ | Signature: ____________________ |
| Name:       ____________________ | Name:       ____________________ |
| Title:      ____________________ | Title:      ____________________ |
| Date:       ____________________ | Date:       ____________________ |

---

*End of MSA Template v1.0 · 2026-06-20. Engineering-drafted; legal counsel must finalise before counter-signature.*
