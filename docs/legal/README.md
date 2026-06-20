# Aegis legal templates

Engineering-drafted contract templates for enterprise procurement.
Every document in this folder is a **template**, not a legally-binding
instrument. Customer counsel and ByteHubble counsel must finalise
before counter-signature.

## What's here

| Document | Purpose | When you need it |
|---|---|---|
| [MSA — Master Service Agreement](msa-template.md) | The master legal framework for every paid engagement. Order Forms attach to it. | Every paid customer |
| [DPA — Data Processing Agreement](dpa-template.md) | GDPR Article 28, India DPDP §8, and equivalents. Engineering controls listed inline with `file:line` citations. | Every EU customer, every data-residency-conscious customer |
| [BAA — Business Associate Agreement](baa-template.md) | HIPAA-Covered-Entity overlay. Supplements (does not replace) the DPA. | Healthcare customers handling PHI |
| [SLA — Service Level Agreement](sla-template.md) | Uptime commitments + service credits + support response targets. Two tiers: 99.5% Design-Partner, 99.9% Enterprise. | Every paid engagement (incorporated by MSA §12.1) |

## Order of precedence

The MSA codifies this (§16.2) but it's worth repeating:

1. **Executed Order Form** — wins on quantities, term, fees
2. **DPA / BAA** — wins on privacy or security topics
3. **SLA** — wins on availability topics
4. **MSA** — controls everything else

## Customer security package

The customer security package builder
(`scripts/ops/build_customer_security_package.sh`) ships all four
templates under section `11_legal/` so a CISO reviewing the package
sees the contractual surface alongside the technical evidence in one
fetch.

## Process

1. Sales hands the four templates to Customer counsel.
2. Customer counsel returns redlines.
3. ByteHubble counsel reviews; engineering reviews any redline that
   touches security or technical claims (§5 of the DPA, §4 of the
   BAA, §3 of the SLA — all of those reference code paths that
   engineering must verify still match).
4. Order Form signed; MSA + DPA + (BAA if applicable) + SLA all
   counter-sign in the same instrument.

## Where the inline `file:line` citations live

The DPA's §5 (Security measures) and the BAA's §4 (Safeguards) cite
specific code paths in the Aegis repository. Those citations are the
heart of the credibility — a CISO can `git log` and `git blame` to
verify the control exists at the version stated. If a citation is
stale (the file moved, the line shifted), file an issue tagged
`legal-citation` and an engineer will refresh both the doc and the
citation in the same PR.

## Versioning

Each template has a `**Version:**` line and a `**Status:**` line.
- **v1.0** files were drafted 2026-06-18 (DPA + BAA).
- **v1.0 / v1.1** files were drafted or refreshed 2026-06-20 (MSA + SLA
  fresh; DPA + BAA refreshed to align sub-processor list and EU
  residency posture from Sprints EI-5 and EI-6).

When a template is updated, bump the minor version, add a one-line
changelog entry to the `**Version:**` field, and update any companion-
doc cross-references.
