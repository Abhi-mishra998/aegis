/**
 * Project scaffolder — `acp init` for the TS SDK.
 *
 * Creates the canonical .acp/ layout in the customer's repo. Mirrors the
 * Python implementation file-for-file (same policy.yaml, parallel example.ts
 * instead of example.py).
 */
import { existsSync, mkdirSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";

export interface InitResult {
  created: string[];
  skipped: string[];
}

export interface InitOptions {
  targetDir: string;
  agentId?: string;
  force?: boolean;
}

const POLICY_TEMPLATE = `# ACP policy file — committed to your repo, validated in CI.
#
# Validate:    acp validate .acp/policy.yaml
# Reference:   https://acp.example.com/docs/policy
#
# Two-section model:
#   allow: agent may invoke these tools (with optional \`when\` predicates)
#   deny:  always rejected, evaluated AFTER allow — deny always wins
#   autonomy: global guardrails on the agent

version: 1
agent: {{AGENT_ID}}

allow:
  # Read-only DB queries OK
  - tool: db.query
    when:
      payload.args.0: "^SELECT"

  # Calls to your own internal services OK
  - tool: http.get
    when:
      payload.args.0: "^https://api\\\\.internal\\\\."

  # Public search OK
  - tool: search

deny:
  # Destructive SQL — always
  - tool: db.query
    when:
      payload.args.0: "DROP|TRUNCATE|DELETE|ALTER"

  # Shell execution — never
  - tool: shell.exec

autonomy:
  max_actions_per_minute: 60
  max_blast_radius: 10
  require_approval_for:
    - send_email
    - transfer_funds
    - delete_user
`;

const EXAMPLE_TEMPLATE = `/**
 * Minimal ACP integration example.
 *
 * Three steps to wrap any async agent function so every call is
 * policy-checked and audit-signed by the gateway:
 *
 *   1. Construct Client (reads ACP_API_KEY + ACP_BASE_URL from env).
 *   2. Wrap the function with client.protect({ agentId: ... }).
 *   3. Call as usual — denials throw DeniedError.
 *
 * Replace the body of \`query\` with your real agent action.
 */
import { Client, DeniedError } from "@acp/sdk";

const client = new Client({
  apiKey: process.env.ACP_API_KEY!,
  baseUrl: process.env.ACP_BASE_URL ?? "https://acp.example.com",
});

const query = client.protect({ agentId: "{{AGENT_ID}}" }, async (sql: string) => {
  // Replace with your real DB call
  return [{ row: 1, sql }];
});

async function main() {
  try {
    const rows = await query("SELECT * FROM users LIMIT 1");
    console.log("ok:", rows);
  } catch (e) {
    if (e instanceof DeniedError) {
      console.warn("blocked by ACP:", e.reason, "decision:", e.decisionId);
    } else {
      throw e;
    }
  }
}

main();
`;

export function initProject(opts: InitOptions): InitResult {
  const target = opts.targetDir;
  const agentId = opts.agentId ?? "agent_default";
  const force = opts.force ?? false;

  if (!existsSync(target)) {
    throw new Error(`target directory does not exist: ${target}`);
  }
  if (!statSync(target).isDirectory()) {
    throw new Error(`target is not a directory: ${target}`);
  }
  if (!agentId) {
    throw new Error("agent_id must be non-empty");
  }

  const acpDir = path.join(target, ".acp");
  mkdirSync(acpDir, { recursive: true });

  const created: string[] = [];
  const skipped: string[] = [];

  const files: [string, string][] = [
    [path.join(acpDir, "policy.yaml"), POLICY_TEMPLATE.replaceAll("{{AGENT_ID}}", agentId)],
    [path.join(acpDir, "example.ts"),  EXAMPLE_TEMPLATE.replaceAll("{{AGENT_ID}}", agentId)],
  ];

  for (const [p, body] of files) {
    if (existsSync(p) && !force) {
      skipped.push(p);
      continue;
    }
    writeFileSync(p, body);
    created.push(p);
  }

  return { created, skipped };
}
