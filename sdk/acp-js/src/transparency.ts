import { createHash } from "node:crypto";
import { canonicalJson } from "./receipts.js";

export interface MerkleSibling {
  side: "L" | "R";
  hash: string;
}

export interface InclusionProof {
  leaf: string;
  index: number;
  siblings: MerkleSibling[];
  root: string;
  size: number;
}

/**
 * Compute the Merkle leaf hash for one signed receipt payload — the same
 * value the server computes when building the daily tree.
 */
export function leafHashForReceipt(signedReceiptPayload: Record<string, unknown>): string {
  return createHash("sha256").update(canonicalJson(signedReceiptPayload)).digest("hex");
}

/**
 * Verify that `leafHex` is included in a Merkle tree whose root is
 * `expectedRoot`. Returns false on any mismatch. Throws on malformed proof
 * so callers can tell "bad input" apart from "valid input, bad signature."
 */
export function verifyInclusion(leafHex: string, proof: InclusionProof, expectedRoot: string): boolean {
  if (!proof || typeof proof !== "object") throw new Error("proof must be a mapping");
  for (const k of ["leaf", "siblings", "root"] as const) {
    if (proof[k] === undefined) throw new Error(`missing field: ${k}`);
  }
  if (proof.leaf !== leafHex) return false;
  if (proof.root !== expectedRoot) return false;

  let cur = Buffer.from(leafHex, "hex");
  for (const sib of proof.siblings) {
    if (sib.side !== "L" && sib.side !== "R") throw new Error("malformed sibling entry");
    if (typeof sib.hash !== "string") throw new Error("malformed sibling entry");
    const sh = Buffer.from(sib.hash, "hex");
    const combined = sib.side === "L" ? Buffer.concat([sh, cur]) : Buffer.concat([cur, sh]);
    cur = createHash("sha256").update(combined).digest();
  }
  return cur.toString("hex") === expectedRoot;
}
