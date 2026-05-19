# @acp/sdk — TypeScript / JavaScript

**Tamper-evident replay + runtime deny for AI agents.**

## Install

```bash
npm install @acp/sdk
```

## Five-line integration

```ts
import { Client } from "@acp/sdk";

const acp = new Client({ apiKey: process.env.ACP_API_KEY, baseUrl: "https://acp.example.com" });

const query = acp.protect({ agentId: "agent_42" }, async (sql: string) => {
  return await db.execute(sql);
});

await query("SELECT * FROM orders");
```

Every call to `query(...)` now:
1. Hits ACP's policy engine before execution. If denied → `DeniedError`, function never runs.
2. Lands in the audit chain with a signed receipt.
3. Is replayable from the Flight Recorder for 90 days.

## Handling denials

```ts
import { DeniedError } from "@acp/sdk";

try {
  await query("DROP TABLE users");
} catch (e) {
  if (e instanceof DeniedError) {
    console.warn("blocked by ACP", e.reason, e.decisionId);
  } else {
    throw e;
  }
}
```

## Policy as code

`.acp/policy.yaml` in your repo:

```yaml
version: 1
agent: agent_42
allow:
  - tool: query
    when:
      payload.args.0: "^SELECT"
deny:
  - tool: query
    when:
      payload.args.0: "DROP|TRUNCATE|DELETE"
autonomy:
  max_actions_per_minute: 60
  require_approval_for: [send_email, transfer_funds]
```

Validate locally:

```bash
npx acp validate .acp/policy.yaml
```

## Replay and verify

```ts
const timeline = await acp.replay("exec_abc123");
const integrity = await acp.verifyAudit();   // cryptographic chain check
```

## Environment

The SDK reads `ACP_API_KEY` and `ACP_BASE_URL` if not passed explicitly.
