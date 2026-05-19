#!/usr/bin/env node
/**
 * acp — CLI for the ACP TypeScript SDK.
 *
 *   acp validate <path>
 *   acp version
 *   acp verify-receipt   <receipt.json> --pubkey <pem>
 *   acp verify-inclusion <inclusion.json> [--root ...] [--leaf ...]
 *   acp verify-bundle    <dir>
 *
 * All verify-* commands exit 0 if everything checks out, 1 if any signature,
 * fingerprint, or Merkle proof fails. Pass --json for machine-readable output.
 */
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import path from "node:path";

import { loadPolicy } from "./policy.js";
import { PolicyError } from "./errors.js";
import { VERSION } from "./index.js";
import { verifyReceipt, type SignedReceiptPayload } from "./receipts.js";
import { leafHashForReceipt, verifyInclusion, type InclusionProof } from "./transparency.js";
import { buildArchive, ArchiveError } from "./archive.js";
import { initProject } from "./init.js";

interface Result {
  ok: boolean;
  lines: string[];
  [k: string]: unknown;
}

function emit(result: Result, asJson: boolean): number {
  if (asJson) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    for (const line of result.lines) console.log(line);
  }
  return result.ok ? 0 : 1;
}

function loadJson(p: string): any {
  try {
    return JSON.parse(readFileSync(p, "utf-8"));
  } catch (e: any) {
    if (e.code === "ENOENT") throw new Error(`error: ${p}: not found`);
    throw new Error(`error: ${p}: invalid JSON: ${e.message}`);
  }
}

function cmdValidate(p: string): number {
  try {
    const policy = loadPolicy(p);
    console.log(`ok: ${p}`);
    console.log(`  version:  ${policy.version}`);
    console.log(`  agent:    ${policy.agent}`);
    console.log(`  allow:    ${policy.allow.length} rule(s)`);
    console.log(`  deny:     ${policy.deny.length} rule(s)`);
    if (policy.autonomy.maxActionsPerMinute !== undefined) {
      console.log(`  autonomy.maxActionsPerMinute: ${policy.autonomy.maxActionsPerMinute}`);
    }
    if (policy.autonomy.requireApprovalFor.length > 0) {
      console.log(`  autonomy.requireApprovalFor:  ${JSON.stringify(policy.autonomy.requireApprovalFor)}`);
    }
    return 0;
  } catch (e) {
    if (e instanceof PolicyError) console.error(`error: ${e.message}`);
    else console.error(`error: ${(e as Error).message}`);
    return 1;
  }
}

function cmdVerifyReceipt(args: { receipt: string; pubkey: string; json: boolean }): number {
  const payload = loadJson(args.receipt) as SignedReceiptPayload;
  let pem: string;
  try {
    pem = readFileSync(args.pubkey, "utf-8");
  } catch {
    return emit({ ok: false, lines: [`error: ${args.pubkey}: public key not found`] }, args.json);
  }
  let ok: boolean;
  try {
    ok = verifyReceipt(payload, pem);
  } catch (e: any) {
    return emit({ ok: false, error: e.message, lines: [`error: ${e.message}`] }, args.json);
  }
  const execId = (payload?.receipt as any)?.execution_id ?? "?";
  const fp = payload?.public_key_fingerprint ?? "?";
  return emit(
    {
      ok,
      execution_id: execId,
      public_key_fingerprint: fp,
      lines: [`${ok ? "OK" : "FAIL"}: receipt ${execId} (fp ${fp})`],
    },
    args.json
  );
}

function cmdVerifyInclusion(args: { inclusion: string; root?: string; leaf?: string; json: boolean }): number {
  const payload = loadJson(args.inclusion);
  const proof: InclusionProof = (payload?.proof ?? payload) as InclusionProof;
  const root = args.root ?? proof.root;
  const leaf = args.leaf ?? proof.leaf;
  if (!root || !leaf) {
    return emit({ ok: false, lines: ["error: --root and --leaf required when not present in proof"] }, args.json);
  }
  let ok: boolean;
  try {
    ok = verifyInclusion(leaf, proof, root);
  } catch (e: any) {
    return emit({ ok: false, error: e.message, lines: [`error: ${e.message}`] }, args.json);
  }
  return emit(
    {
      ok,
      root,
      leaf,
      index: proof.index,
      size: proof.size,
      lines: [
        `${ok ? "OK" : "FAIL"}: inclusion of leaf at index ${proof.index} of ${proof.size} → root ${root.slice(0, 16)}…`,
      ],
    },
    args.json
  );
}

function cmdVerifyBundle(args: { bundle: string; json: boolean }): number {
  const bundle = args.bundle;
  if (!existsSync(bundle) || !statSync(bundle).isDirectory()) {
    return emit({ ok: false, lines: [`error: ${bundle}: not a directory`] }, args.json);
  }
  const pubPath = path.join(bundle, "public_key.pem");
  if (!existsSync(pubPath)) {
    return emit({ ok: false, lines: [`error: ${pubPath}: not found`] }, args.json);
  }
  const pem = readFileSync(pubPath, "utf-8");

  const receiptsDir = path.join(bundle, "receipts");
  const inclusionDir = path.join(bundle, "inclusion");
  const rootsDir = path.join(bundle, "roots");

  const receiptFiles = existsSync(receiptsDir)
    ? readdirSync(receiptsDir).filter((f) => f.endsWith(".json")).sort()
    : [];
  if (receiptFiles.length === 0) {
    return emit({ ok: false, lines: [`error: no receipts under ${receiptsDir}`] }, args.json);
  }

  const inclusionIndex: Record<string, string> = {};
  if (existsSync(inclusionDir)) {
    for (const f of readdirSync(inclusionDir).filter((x) => x.endsWith(".json"))) {
      inclusionIndex[path.basename(f, ".json")] = path.join(inclusionDir, f);
    }
  }
  const rootIndex: Record<string, string> = {};
  if (existsSync(rootsDir)) {
    for (const f of readdirSync(rootsDir).filter((x) => x.endsWith(".json"))) {
      rootIndex[path.basename(f, ".json")] = path.join(rootsDir, f);
    }
  }

  const counts = {
    receipts: 0,
    receipts_ok: 0,
    inclusion_checked: 0,
    inclusion_ok: 0,
    root_anchored: 0,
    root_matches: 0,
  };
  const failures: string[] = [];
  const detail: string[] = [];

  for (const rf of receiptFiles) {
    counts.receipts++;
    const rfile = path.join(receiptsDir, rf);
    const payload = loadJson(rfile) as SignedReceiptPayload;
    const execId = (payload.receipt as any)?.execution_id ?? path.basename(rf, ".json");

    let recOk: boolean;
    try {
      recOk = verifyReceipt(payload, pem);
    } catch (e: any) {
      failures.push(`${rfile}: malformed receipt: ${e.message}`);
      continue;
    }
    if (!recOk) {
      failures.push(`${rfile}: signature INVALID`);
      continue;
    }
    counts.receipts_ok++;

    const stem = path.basename(rf, ".json");
    const ipath = inclusionIndex[stem] ?? inclusionIndex[execId];
    if (!ipath) {
      detail.push(`  OK   receipt   ${execId} (no inclusion proof archived)`);
      continue;
    }
    counts.inclusion_checked++;
    const inclusionPayload = loadJson(ipath);
    const proof: InclusionProof = (inclusionPayload.proof ?? inclusionPayload) as InclusionProof;
    const leaf = leafHashForReceipt(payload as unknown as Record<string, unknown>);
    let incOk: boolean;
    try {
      incOk = verifyInclusion(leaf, proof, proof.root);
    } catch (e: any) {
      failures.push(`${ipath}: malformed proof: ${e.message}`);
      continue;
    }
    if (!incOk) {
      failures.push(`${ipath}: inclusion proof INVALID`);
      continue;
    }
    counts.inclusion_ok++;

    const rootDate = inclusionPayload.root_date ?? "";
    const rootFile = rootIndex[rootDate];
    if (rootFile) {
      counts.root_anchored++;
      const rootPayload = loadJson(rootFile);
      const signed = rootPayload.signed ?? rootPayload;
      const signedRootHash = signed?.receipt?.root_hash;
      if (signedRootHash === proof.root) {
        counts.root_matches++;
        detail.push(`  OK   anchored ${execId} → root ${proof.root.slice(0, 16)}… (${rootDate})`);
      } else {
        failures.push(`${ipath}: proof root != signed daily root for ${rootDate}`);
      }
    } else {
      detail.push(`  OK   included ${execId} (no signed daily root archived for ${rootDate || "?"})`);
    }
  }

  const ok = failures.length === 0;
  const lines = [
    `bundle: ${bundle}`,
    `  receipts:             ${counts.receipts_ok}/${counts.receipts}`,
    `  inclusion proofs:     ${counts.inclusion_ok}/${counts.inclusion_checked}`,
    `  daily-root anchored:  ${counts.root_matches}/${counts.root_anchored}`,
    ...detail,
  ];
  if (failures.length) {
    lines.push("", "FAILURES:");
    for (const f of failures) lines.push(`  - ${f}`);
  }
  lines.push("", ok ? "OK" : "FAIL");

  return emit({ ok, counts, failures, lines }, args.json);
}

function cmdInit(args: { dir: string; agentId: string; force: boolean; json: boolean }): number {
  let result;
  try {
    result = initProject({ targetDir: args.dir, agentId: args.agentId, force: args.force });
  } catch (e: any) {
    return emit({ ok: false, error: e.message, lines: [`error: ${e.message}`] }, args.json);
  }
  const lines: string[] = [];
  for (const p of result.created) lines.push(`  created  ${p}`);
  for (const p of result.skipped) lines.push(`  skipped  ${p} (already exists; pass --force to overwrite)`);
  lines.push("");
  if (result.created.length > 0) {
    lines.push("Next steps:");
    lines.push("  1. Set ACP_API_KEY and ACP_BASE_URL in your environment");
    lines.push("  2. Customize .acp/policy.yaml for your agent");
    lines.push("  3. Run: acp validate .acp/policy.yaml");
    lines.push("  4. Wire .acp/example.ts into your codebase");
  } else {
    lines.push("No new files created. Pass --force to overwrite the scaffold.");
  }
  return emit({ ok: true, created: result.created, skipped: result.skipped, lines }, args.json);
}

async function cmdArchive(args: {
  baseUrl: string;
  token: string;
  out: string;
  tenant?: string;
  since?: string;
  until?: string;
  limit?: number;
  json: boolean;
}): Promise<number> {
  let counts;
  try {
    counts = await buildArchive({
      baseUrl: args.baseUrl,
      token: args.token,
      outDir: args.out,
      tenant: args.tenant,
      since: args.since,
      until: args.until,
      limit: args.limit,
    });
  } catch (e) {
    if (e instanceof ArchiveError) {
      return emit({ ok: false, error: e.message, lines: [`error: ${e.message}`] }, args.json);
    }
    throw e;
  }
  return emit(
    {
      ok: true,
      out: args.out,
      counts,
      lines: [
        `archive: ${args.out}`,
        `  receipts written:  ${counts.receipts}`,
        `  inclusion proofs:  ${counts.inclusion}`,
        `  daily roots:       ${counts.roots}`,
        "",
        `Verify with:  acp verify-bundle ${args.out}`,
      ],
    },
    args.json
  );
}

function parseFlags(argv: string[]): { positional: string[]; flags: Record<string, string | boolean> } {
  const flags: Record<string, string | boolean> = {};
  const positional: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) {
        flags[key] = true;
      } else {
        flags[key] = next;
        i++;
      }
    } else {
      positional.push(a);
    }
  }
  return { positional, flags };
}

function usage(): number {
  console.error(
    "usage: acp <validate <path> | version | verify-receipt <r.json> --pubkey <pem> | " +
      "verify-inclusion <i.json> [--root --leaf] | verify-bundle <dir> | " +
      "archive --base-url ... --token ... --out ... [--tenant --since --until --limit] | " +
      "init [--dir .] [--agent-id ...] [--force]> [--json]"
  );
  return 2;
}

async function main(argv: string[]): Promise<number> {
  const [cmd, ...rest] = argv;
  const { positional, flags } = parseFlags(rest);
  switch (cmd) {
    case "validate":
      if (!positional[0]) return usage();
      return cmdValidate(positional[0]);
    case "version":
      console.log(`acp ${VERSION}`);
      return 0;
    case "verify-receipt":
      if (!positional[0] || !flags.pubkey) return usage();
      return cmdVerifyReceipt({
        receipt: positional[0],
        pubkey: String(flags.pubkey),
        json: Boolean(flags.json),
      });
    case "verify-inclusion":
      if (!positional[0]) return usage();
      return cmdVerifyInclusion({
        inclusion: positional[0],
        root: flags.root === true ? undefined : (flags.root as string | undefined),
        leaf: flags.leaf === true ? undefined : (flags.leaf as string | undefined),
        json: Boolean(flags.json),
      });
    case "verify-bundle":
      if (!positional[0]) return usage();
      return cmdVerifyBundle({ bundle: positional[0], json: Boolean(flags.json) });
    case "init":
      return cmdInit({
        dir: flags.dir === true || flags.dir === undefined ? "." : String(flags.dir),
        agentId: flags["agent-id"] === true || flags["agent-id"] === undefined ? "agent_default" : String(flags["agent-id"]),
        force: Boolean(flags.force),
        json: Boolean(flags.json),
      });
    case "archive":
      if (!flags["base-url"] || !flags.token || !flags.out) return usage();
      return await cmdArchive({
        baseUrl: String(flags["base-url"]),
        token: String(flags.token),
        out: String(flags.out),
        tenant: flags.tenant === true ? undefined : (flags.tenant as string | undefined),
        since: flags.since === true ? undefined : (flags.since as string | undefined),
        until: flags.until === true ? undefined : (flags.until as string | undefined),
        limit: flags.limit === true || flags.limit === undefined ? undefined : Number(flags.limit),
        json: Boolean(flags.json),
      });
    default:
      return usage();
  }
}

main(process.argv.slice(2)).then((code) => process.exit(code));
