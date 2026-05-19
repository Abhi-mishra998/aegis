import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { generateKeyPairSync, sign as cryptoSign } from "node:crypto";

import { verifyReceipt, canonicalJson, fingerprintPublicKey } from "../src/receipts.js";

const FIXTURE_PATH = new URL("./fixtures/python_signed_receipt.json", import.meta.url);

test("TS verifier accepts a Python-signed receipt (cross-language proof)", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf-8")) as {
    payload: any;
    public_key_pem: string;
  };
  const ok = verifyReceipt(fixture.payload, fixture.public_key_pem);
  assert.equal(ok, true);
});

test("TS verifier rejects a tampered Python-signed receipt", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf-8")) as {
    payload: any;
    public_key_pem: string;
  };
  fixture.payload.receipt.decision = "deny";
  assert.equal(verifyReceipt(fixture.payload, fixture.public_key_pem), false);
});

test("TS roundtrip: sign with Node + verify with Node", () => {
  const { privateKey, publicKey } = generateKeyPairSync("ed25519");
  const pubPem = publicKey.export({ format: "pem", type: "spki" }).toString();

  const receipt = {
    version: 1,
    execution_id: "e1",
    decision: "allow",
    tool: "x",
  };
  const sig = cryptoSign(null, canonicalJson(receipt), privateKey).toString("base64url").replace(/=+$/, "");

  const payload = {
    receipt,
    signature: sig,
    algorithm: "ed25519",
    public_key_fingerprint: fingerprintPublicKey(Buffer.from(pubPem, "ascii")),
  };

  assert.equal(verifyReceipt(payload, pubPem), true);

  payload.receipt.decision = "deny";
  assert.equal(verifyReceipt(payload, pubPem), false);
});

test("rejects unknown algorithm", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf-8")) as {
    payload: any;
    public_key_pem: string;
  };
  fixture.payload.algorithm = "rsa";
  assert.throws(
    () => verifyReceipt(fixture.payload, fixture.public_key_pem),
    /unsupported algorithm/
  );
});

test("rejects fingerprint mismatch", () => {
  const fixture = JSON.parse(readFileSync(FIXTURE_PATH, "utf-8")) as {
    payload: any;
    public_key_pem: string;
  };
  fixture.payload.public_key_fingerprint = "0".repeat(32);
  assert.equal(verifyReceipt(fixture.payload, fixture.public_key_pem), false);
});
