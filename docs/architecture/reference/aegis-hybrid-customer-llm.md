# Aegis hybrid — customer-controlled LLM, Aegis-controlled governance

**Audience:** Customer Cloud Architect whose **prompts and completions
cannot leave their VPC** for regulatory reasons (financial services
trade-secret protection, healthcare PHI, defence ITAR, EU GDPR
heightened scrutiny). **Status:** Supported today via Path A — Aegis
sees the tool-call shape, never the prompt body. Reference architecture
for the deployment pattern.

The premise: most "Aegis governs my AI" pitches assume Aegis proxies
the LLM call (Path B — `/v1/messages`, `/v1/chat/completions`). For
regulated customers that's a non-starter — they need the LLM call to
remain inside their VPC. Path A is the answer.

---

## 1. Two paths, one decision

```
                            ┌────────────────────┐
                            │  Customer Agent    │
                            │  (LangChain /      │
                            │   Bedrock SDK /    │
                            │   custom)          │
                            └────────┬───────────┘
                                     │
                ┌────────────────────┼─────────────────────┐
                │                                          │
                ▼                                          ▼
        ┌───────────────┐                          ┌──────────────────┐
        │  Path A       │                          │  Path B          │
        │  /execute     │                          │  /v1/messages    │
        │               │                          │  /v1/chat/...    │
        │  Aegis sees   │                          │  Aegis sees the  │
        │  tool name +  │                          │  prompt body +   │
        │  args shape;  │                          │  LLM response;   │
        │  prompt body  │                          │  proxies to      │
        │  NEVER leaves │                          │  Anthropic /     │
        │  customer VPC │                          │  OpenAI          │
        └───────┬───────┘                          └────────┬─────────┘
                │                                           │
                │                                           ▼
                │                                  ┌────────────────────┐
                │                                  │  Anthropic / OpenAI │
                │                                  │  on Aegis's account │
                │                                  └─────────────────────┘
                ▼
        ┌────────────────┐
        │  Customer's    │
        │  own LLM:      │
        │  - Bedrock     │
        │  - Azure       │
        │    OpenAI      │
        │  - Local       │
        │    llama.cpp   │
        │  - On-prem GPU │
        └────────────────┘
```

Both paths flow through the same Aegis decision pipeline (OPA + policy
service + audit chain). The difference is **what crosses the trust
boundary**.

---

## 2. Path A — what Aegis actually sees

For every tool call the agent wants to make, the customer SDK or
gateway POSTs to Aegis's `/execute`:

```http
POST https://aegisagent.in/execute
Authorization: Bearer <tenant API key>
Content-Type: application/json

{
  "tool": "wire_transfer",
  "payload": {
    "amount_usd": 250000,
    "recipient": "external-vendor-xyz",
    "memo":       "Q3 invoice"
  },
  "agent_id": "agt-finance-bot-001"
}
```

Aegis evaluates: deny / allow / escalate / quarantine based on the OPA
rules + signal registry + per-agent rate-limits. Returns:

```json
{
  "decision":    "escalate",
  "risk_score":  72,
  "findings":    ["money_transfer_external", "amount_above_floor"],
  "explanation": "External wire above $100k requires human approval"
}
```

**Aegis never saw**:
- The system prompt (whatever instructed the agent to call wire_transfer)
- The user's natural-language input that produced this tool call
- The conversation history
- Any completion text from the LLM

**Aegis did see**:
- The tool name (`wire_transfer`)
- The tool arguments (amount, recipient, memo — these are by definition
  payload Aegis must inspect, the customer can choose to redact-before-
  send for highly-sensitive fields)
- Agent identity (which agent in which tenant)

The customer's agent enforces the decision on its own side — if Aegis
returns deny, the agent doesn't call the underlying API. If escalate,
the agent waits for `/v1/approvals` to flip to approved.

---

## 3. Where the LLM lives — three patterns

### 3.1 Pattern A1 — Bedrock in customer's AWS

```
   ┌─────────────────── Customer's AWS account ──────────────────┐
   │                                                              │
   │   ┌───────────────┐         ┌────────────────────────────┐   │
   │   │ Customer EC2  │         │   AWS Bedrock              │   │
   │   │ runs agent    │◄────────│   (Anthropic / Llama /     │   │
   │   │               │ inside  │    Mistral via private     │   │
   │   │ aegis-bedrock │ VPC     │    Bedrock endpoint)       │   │
   │   │ SDK           │         └────────────────────────────┘   │
   │   └───────┬───────┘                                          │
   │           │  POST /execute (tool-call shape, no prompt body) │
   └───────────┼──────────────────────────────────────────────────┘
               │
               ▼
       ┌───────────────┐
       │ Aegis cloud   │
       │ (ap-south-1   │
       │  or eu-west-1)│
       └───────────────┘
```

Aegis ships the `aegis-bedrock` SDK (PyPI). The customer's agent calls
`bedrock-runtime.invoke_model()` against their **own** Bedrock endpoint;
the SDK wraps the call so the resulting tool-use blocks are sent to
Aegis as `/execute` calls. Prompts + completions stay inside the
customer's VPC.

### 3.2 Pattern A2 — Azure OpenAI in customer's Azure

Same shape, different provider. Customer enables Azure OpenAI in their
own Azure subscription (gives them per-customer DPA + EU residency
options); the agent's call to Azure OpenAI happens entirely within their
tenant. Aegis sees only the tool-call shape via `/execute`.

The `aegis-langchain` SDK (PyPI) covers this — LangChain's `AzureChatOpenAI`
+ `bind_tools` integration plumbs the tool-call summary into Aegis.

### 3.3 Pattern A3 — On-prem llama.cpp / vLLM / Ollama

Strictest pattern. Customer runs the LLM weights on their own GPU
hardware (often air-gapped + no internet egress). The agent runs
inside the same network; calls Aegis via an HTTPS proxy whitelist
that allows ONE host: `aegisagent.in`. Tool-call shape over that.
Prompt body never reaches the public internet.

Customers in this pattern usually use the EU instance
(`eu.aegisagent.in`) + restrict egress to that one hostname; Aegis's
TLS cert + Cloudfront / ALB IPs are the only external dependency.

---

## 4. Trust-boundary verification

A reviewer can verify Aegis never sees the prompt body by grepping
our source for any LLM-provider client outside of the Path B handlers:

```bash
# Anthropic / OpenAI / Bedrock SDKs are imported ONLY from these files:
grep -rln "import anthropic\|from anthropic\|openai\.\|boto3.*bedrock" \
  services/ sdk/ | grep -v __pycache__ | grep -v tests
# Expected output: only services/gateway/routers/messages.py +
# services/gateway/routers/openai_messages.py + sdk/aegis_bedrock/
# (the SDK customer chooses to install or not). All three are Path B.
```

Path A's `/execute` handler (`services/gateway/middleware.py` +
`services/policy/` + `services/decision/`) has zero LLM-client imports.

---

## 5. What Aegis IS allowed to see, even on Path A

Be honest with the customer's reviewer:

| Aegis can see | Why |
|---|---|
| **Tool name** | Required — policy rules key on `tool == "wire_transfer"` |
| **Tool args** | Required — policy rules key on `amount_usd > 100000`. Customer SHOULD redact ultra-sensitive arg fields (SSNs, account numbers) before send if their compliance posture demands it. |
| **Agent identity** | Required — per-agent rate-limits + behavioral baseline |
| **Tenant identity** | Required — every audit row + every policy lookup |
| **Timestamp** | Required — rate-limit windows, dedup keys |
| **Source IP** | Optional — set if X-Forwarded-For is forwarded; useful for forensics |

What Aegis is contractually committed NOT to see on Path A:

- Prompt body (system + user + assistant)
- Completion text
- Model name / temperature / token counts
- Conversation history beyond the current tool call
- Any data the customer didn't explicitly put into a tool argument

The `services/audit/redact.py` field-level redaction layer applies on
Aegis's side too: SSN-shaped, credit-card-shaped, JWT-shaped fields are
hashed before writing to audit_logs even if the customer accidentally
sends them.

---

## 6. Latency budget

Path A adds one HTTPS round-trip per tool call:

| Hop | Median | p99 | Notes |
|---|---|---|---|
| Customer agent → Aegis `/execute` | 30 ms (regional) / 150 ms (cross-region) | 200 ms / 400 ms | Driven by network latency from customer VPC to Aegis region; co-location helps |
| Aegis decision pipeline (OPA + policy + signal registry + audit write) | 21 ms (measured, dry-run) | 40 ms | Documented in §3 of the business doc |
| Aegis → customer agent (response) | same return path | same | |
| **Total added latency** | **~80 ms** (same-region) | **~250 ms** | Per tool call |

For agents that fire 1-5 tool calls per user-facing request, the added
latency is in the 100-500 ms range — noticeable but acceptable for
non-real-time use cases (financial back-office, code generation,
healthcare admin workflows). Real-time use cases (chatbot answering
in <2s) should profile carefully; the Path A cost is real.

---

## 7. Cost story

Path A's compute cost on Aegis's side is the same as Path B per
request (same decision pipeline; both use the same OPA + Postgres +
Redis path). The **customer pays** for the LLM tokens directly to
Bedrock / Azure / their on-prem GPU — Aegis doesn't mark them up.

For a customer with 10 million tool calls per month:

- Aegis Enterprise tier (per `docs/legal/sla-template.md` + Stripe
  price IDs): ~$5k/month flat
- Their own LLM cost (e.g. Claude Sonnet 4.5 on Bedrock at $3/$15
  per 1M tokens, ~500 tokens per tool call): ~$15k-30k/month
- **Aegis is ~20-30% of the total stack cost** for a customer at
  that scale; the LLM is the bigger line item. Path A doesn't change
  this — the LLM cost is the same whether Aegis proxies or not.

---

## 8. Compliance frameworks this pattern satisfies

| Framework | Why Path A helps |
|---|---|
| **GDPR Art. 28** (data processor minimisation) | Aegis processes only the minimum necessary — tool-call shape, not prompt body — to perform governance |
| **GDPR Art. 32** (security of processing) | The data Aegis sees is a strict subset of what the customer's LLM provider sees; reduces the blast radius of any Aegis compromise |
| **HIPAA min-necessary (§ 164.514(d))** | Aegis BAA (`docs/legal/baa-template.md` §3.2) commits to this; Path A makes it provable — the prompt body containing PHI never reaches Aegis |
| **EU AI Act Art. 12** (audit records) | Aegis's audit chain proves what tool calls the agent attempted; the customer's own audit covers what prompts produced those calls |
| **India DPDP §8(5)** (record retention) | Same as GDPR — Aegis stores only what it inspected |

---

## 9. What this pattern does NOT solve

Be honest with the customer's reviewer:

- **It does not protect against an evil customer.** If the customer's
  agent is intentionally configured to NOT call Aegis, Aegis sees
  nothing. The audit chain only covers what gets sent to it. Path A
  protects against accidental data exposure to Aegis; it does not
  protect against the customer deliberately bypassing Aegis.
- **It does not stop a prompt-injection attack inside the customer's
  VPC.** Aegis evaluates the tool call AFTER the LLM has produced it.
  If the LLM was prompt-injected into producing a malicious tool call,
  Aegis catches the malicious tool call (assuming policy covers it)
  but cannot prevent the LLM from being injected in the first place.
- **It does not give the customer's auditor visibility into prompt
  content.** That's by design — but the customer's auditor often wants
  it. The fix is to log prompt content on the customer's side (their
  LLM provider's logging, e.g. Bedrock Model Invocation Logging) and
  cross-reference with Aegis's audit chain via the per-tool-call
  request_id.
