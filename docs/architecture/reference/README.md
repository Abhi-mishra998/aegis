# Aegis — Reference architectures

Three deployment patterns + the trade-offs that decide between them.
All three are reviewable end-to-end by a F500 Cloud Architect doing
vendor design review.

| Pattern | Doc | Status | When to choose |
|---|---|---|---|
| **Aegis on AWS** | [`aegis-on-aws.md`](aegis-on-aws.md) | **Production** at `aegisagent.in` + `eu.aegisagent.in` | Default. The source-of-truth implementation. |
| **Aegis on Azure** | [`aegis-on-azure.md`](aegis-on-azure.md) | Target architecture; Terraform on roadmap | Customer mandates "must run in our Azure tenant". Engineering ships the Terraform sprint when first contract lands. |
| **Aegis hybrid — customer LLM** | [`aegis-hybrid-customer-llm.md`](aegis-hybrid-customer-llm.md) | **Production** via Path A `/execute` + `aegis-bedrock` / `aegis-langchain` SDKs | Customer mandates "prompts + completions cannot leave our VPC". Most regulated finance / healthcare / defence customers land here. |

## How to use these for a vendor design review

1. Architect reads `aegis-on-aws.md` end-to-end (~20 min). Every box
   in §1 cites the Terraform module that creates it.
2. Architect spot-checks 3-4 of the cited modules in
   `infra/terraform/modules/` against the doc claims. If they
   reconcile, the document is credible.
3. Architect picks the deployment pattern that matches their
   constraint (AWS / Azure / hybrid).
4. For Azure: architect understands today's Terraform is AWS-only;
   the Azure stack is a known sprint when a contract is signed.
5. For hybrid: architect runs the `grep` recipe at §4 of
   `aegis-hybrid-customer-llm.md` to verify Aegis has no LLM-client
   import outside the Path B handlers.

## Companion documents

- [`docs/security/data_residency.md`](../../security/data_residency.md) —
  per-data-class residency table; reconciles with §5 of each
  reference architecture.
- [`docs/adr/README.md`](../../adr/README.md) — 10 Architecture Decision
  Records explaining the design choices these architectures embody.
- [`docs/legal/msa-template.md`](../../legal/msa-template.md) +
  [`dpa-template.md`](../../legal/dpa-template.md) — the contractual
  shape that pairs with each architecture.
