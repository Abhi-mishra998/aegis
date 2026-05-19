import { readFileSync } from "node:fs";
import { parse as parseYaml } from "yaml";
import { PolicyError } from "./errors.js";

export interface Rule {
  tool: string;
  when: Record<string, string>;
}

export interface Autonomy {
  maxActionsPerMinute?: number;
  maxBlastRadius?: number;
  requireApprovalFor: string[];
}

export interface Policy {
  version: number;
  agent: string;
  allow: Rule[];
  deny: Rule[];
  autonomy: Autonomy;
}

const SUPPORTED_VERSIONS = new Set<number>([1]);
const VALID_TOP_LEVEL = new Set<string>(["version", "agent", "allow", "deny", "autonomy"]);

export function loadPolicy(path: string): Policy {
  let raw: unknown;
  try {
    raw = parseYaml(readFileSync(path, "utf-8"));
  } catch (e) {
    throw new PolicyError(`invalid YAML in ${path}: ${(e as Error).message}`);
  }
  return validatePolicy(raw);
}

export function validatePolicy(raw: unknown): Policy {
  if (!isObject(raw)) throw new PolicyError("policy root must be a mapping");

  const unknown = Object.keys(raw).filter((k) => !VALID_TOP_LEVEL.has(k));
  if (unknown.length > 0) {
    throw new PolicyError(`unknown top-level keys: ${JSON.stringify(unknown.sort())}`);
  }

  const version = raw["version"];
  if (typeof version !== "number" || !SUPPORTED_VERSIONS.has(version)) {
    throw new PolicyError(`unsupported policy version: ${JSON.stringify(version)}`);
  }

  const agent = raw["agent"];
  if (typeof agent !== "string" || agent.length === 0) {
    throw new PolicyError("`agent` must be a non-empty string");
  }

  const allow = parseRuleList(raw["allow"], "allow");
  const deny = parseRuleList(raw["deny"], "deny");

  const autonomyRaw = raw["autonomy"];
  if (autonomyRaw !== undefined && !isObject(autonomyRaw)) {
    throw new PolicyError("`autonomy` must be a mapping");
  }
  const ar = (autonomyRaw ?? {}) as Record<string, unknown>;
  const autonomy: Autonomy = {
    maxActionsPerMinute: typeof ar["max_actions_per_minute"] === "number" ? (ar["max_actions_per_minute"] as number) : undefined,
    maxBlastRadius: typeof ar["max_blast_radius"] === "number" ? (ar["max_blast_radius"] as number) : undefined,
    requireApprovalFor: Array.isArray(ar["require_approval_for"]) ? (ar["require_approval_for"] as string[]) : [],
  };

  return { version, agent, allow, deny, autonomy };
}

function parseRuleList(raw: unknown, kind: string): Rule[] {
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) throw new PolicyError(`\`${kind}\` must be a list`);
  return raw.map((r, i) => parseRule(r, kind, i));
}

function parseRule(raw: unknown, kind: string, idx: number): Rule {
  if (!isObject(raw)) throw new PolicyError(`${kind}[${idx}] must be a mapping`);
  const tool = raw["tool"];
  if (typeof tool !== "string" || tool.length === 0) {
    throw new PolicyError(`${kind}[${idx}].tool must be a non-empty string`);
  }
  const whenRaw = raw["when"];
  if (whenRaw !== undefined && !isObject(whenRaw)) {
    throw new PolicyError(`${kind}[${idx}].when must be a mapping`);
  }
  const when: Record<string, string> = {};
  for (const [k, v] of Object.entries(whenRaw ?? {})) {
    if (typeof v !== "string") {
      throw new PolicyError(`${kind}[${idx}].when.${k} must be a string regex`);
    }
    try {
      new RegExp(v);
    } catch (e) {
      throw new PolicyError(`${kind}[${idx}].when.${k}: invalid regex: ${(e as Error).message}`);
    }
    when[k] = v;
  }
  return { tool, when };
}

function isObject(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}
