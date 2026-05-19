import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";

const ALGORITHM = "ed25519";

export interface SignedReceiptPayload {
  receipt: Record<string, unknown>;
  signature: string;
  algorithm: string;
  public_key_fingerprint: string;
}

/** Stable canonical JSON: sorted keys, compact separators, UTF-8. */
export function canonicalJson(obj: Record<string, unknown>): Buffer {
  return Buffer.from(stableStringify(obj), "utf-8");
}

export function fingerprintPublicKey(pemBytes: Buffer): string {
  return createHash("sha256").update(pemBytes).digest("hex").slice(0, 32);
}

function base64UrlDecode(s: string): Buffer {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  return Buffer.from(s + pad, "base64url");
}

/**
 * Verify a signed-receipt payload from /v1/receipts/:id against a public key.
 *
 * Returns true iff signature, fingerprint, and canonical encoding all agree.
 * Throws on malformed input so callers can tell "bad payload" from
 * "valid payload, bad signature."
 */
export function verifyReceipt(payload: SignedReceiptPayload, publicKeyPem: string): boolean {
  for (const k of ["receipt", "signature", "algorithm", "public_key_fingerprint"] as const) {
    if (payload[k] === undefined) {
      throw new Error(`missing field: ${k}`);
    }
  }
  if (payload.algorithm !== ALGORITHM) {
    throw new Error(`unsupported algorithm: ${payload.algorithm}`);
  }

  const pemBytes = Buffer.from(publicKeyPem, "ascii");
  if (fingerprintPublicKey(pemBytes) !== payload.public_key_fingerprint) {
    return false;
  }

  let pubKey;
  try {
    pubKey = createPublicKey(publicKeyPem);
  } catch (e) {
    throw new Error(`invalid public key PEM: ${(e as Error).message}`);
  }
  if (pubKey.asymmetricKeyType !== "ed25519") {
    throw new Error("public key is not ed25519");
  }

  try {
    return cryptoVerify(
      null, // algorithm is determined by the key type for ed25519
      canonicalJson(payload.receipt),
      pubKey,
      base64UrlDecode(payload.signature)
    );
  } catch {
    return false;
  }
}

/** Sort keys deterministically, no whitespace — must match Python's canonical_json. */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return "[" + value.map(stableStringify).join(",") + "]";
  }
  const keys = Object.keys(value as Record<string, unknown>).sort();
  return (
    "{" +
    keys
      .map((k) => JSON.stringify(k) + ":" + stableStringify((value as Record<string, unknown>)[k]))
      .join(",") +
    "}"
  );
}
