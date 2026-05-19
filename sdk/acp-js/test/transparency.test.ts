import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";

import { verifyInclusion, leafHashForReceipt, type InclusionProof } from "../src/transparency.js";

const FIXTURE = new URL("./fixtures/python_built_tree.json", import.meta.url);

interface Fixture {
  payloads: Record<string, unknown>[];
  leaves: string[];
  root: string;
  proofs: InclusionProof[];
}

test("TS verifies inclusion in a Python-built Merkle tree (cross-language proof)", () => {
  const f = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Fixture;
  for (let i = 0; i < f.leaves.length; i++) {
    assert.equal(verifyInclusion(f.leaves[i]!, f.proofs[i]!, f.root), true, `leaf ${i}`);
  }
});

test("TS leafHashForReceipt matches Python-computed leaf for every receipt", () => {
  const f = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Fixture;
  for (let i = 0; i < f.payloads.length; i++) {
    const computed = leafHashForReceipt(f.payloads[i]!);
    assert.equal(computed, f.leaves[i], `mismatch at i=${i}`);
  }
});

test("TS rejects tampered proof", () => {
  const f = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Fixture;
  const proof = JSON.parse(JSON.stringify(f.proofs[2])) as InclusionProof;
  proof.siblings[0]!.hash = "f".repeat(64);
  assert.equal(verifyInclusion(f.leaves[2]!, proof, f.root), false);
});

test("TS rejects swapped leaf", () => {
  const f = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Fixture;
  // Claim leaf 3 is at index 2 — proof should reject
  assert.equal(verifyInclusion(f.leaves[3]!, f.proofs[2]!, f.root), false);
});

test("TS rejects wrong root", () => {
  const f = JSON.parse(readFileSync(FIXTURE, "utf-8")) as Fixture;
  assert.equal(verifyInclusion(f.leaves[0]!, f.proofs[0]!, "0".repeat(64)), false);
});

test("TS raises on malformed proof", () => {
  assert.throws(
    () => verifyInclusion("a".repeat(64), { leaf: "a".repeat(64) } as any, "b".repeat(64)),
    /missing field/
  );
});

test("TS roundtrip: simple 3-leaf tree built and verified in Node", () => {
  // Sanity check the algorithm internals match by replicating a small tree
  const leaves = ["00", "01", "02"].map((s) => createHash("sha256").update(s).digest("hex"));
  // Mirror the Python implementation's odd-duplication
  let level = leaves.map((h) => Buffer.from(h, "hex"));
  if (level.length % 2 === 1) level.push(level[level.length - 1]!);
  const lvl2 = [
    createHash("sha256").update(Buffer.concat([level[0]!, level[1]!])).digest(),
    createHash("sha256").update(Buffer.concat([level[2]!, level[3]!])).digest(),
  ];
  const root = createHash("sha256").update(Buffer.concat([lvl2[0]!, lvl2[1]!])).digest("hex");

  // Build proof for leaf 0 by hand
  const proof: InclusionProof = {
    leaf: leaves[0]!,
    index: 0,
    siblings: [
      { side: "R", hash: level[1]!.toString("hex") },
      { side: "R", hash: lvl2[1].toString("hex") },
    ],
    root,
    size: 3,
  };
  assert.equal(verifyInclusion(leaves[0]!, proof, root), true);
});
