import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, existsSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { buildArchive, ArchiveError } from "../src/archive.js";

interface StubServer {
  fetchImpl: typeof fetch;
  requests: string[];
}

function buildStubServer(opts: { receipts: number; rootDate: string }): StubServer {
  const requests: string[] = [];
  const receiptIds = Array.from({ length: opts.receipts }, (_, i) =>
    `00000000-0000-0000-0000-${String(i + 1).padStart(12, "0")}`
  );

  const fakePem = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAAQID\n-----END PUBLIC KEY-----\n";
  const fingerprint = "deadbeefdeadbeefdeadbeefdeadbeef";

  const fakeReceipt = (id: string) => ({
    receipt: { execution_id: id, version: 1, decision: "allow" },
    signature: "stub",
    algorithm: "ed25519",
    public_key_fingerprint: fingerprint,
  });

  const fakeInclusion = (id: string) => ({
    root_date: opts.rootDate,
    proof: {
      leaf: "a".repeat(64),
      index: receiptIds.indexOf(id),
      siblings: [],
      root: "b".repeat(64),
      size: receiptIds.length,
    },
    pending: false,
  });

  const fakeRoot = {
    root_date: opts.rootDate,
    root_hash: "b".repeat(64),
    leaf_count: opts.receipts,
    signed: { receipt: { root_hash: "b".repeat(64) }, signature: "x", algorithm: "ed25519", public_key_fingerprint: fingerprint },
  };

  const fetchImpl: typeof fetch = async (input, _init) => {
    const url = typeof input === "string" ? input : input.toString();
    requests.push(url);
    const pathname = new URL(url).pathname;

    if (pathname === "/v1/receipts/key") {
      return new Response(JSON.stringify({ public_key_pem: fakePem, fingerprint, algorithm: "ed25519" }), { status: 200 });
    }
    if (pathname === "/v1/audit/export") {
      const ndjson = receiptIds.map((id, i) =>
        JSON.stringify({ id, timestamp: `${opts.rootDate}T10:0${i}:00+00:00` })
      ).join("\n") + "\n";
      return new Response(ndjson, { status: 200, headers: { "content-type": "application/x-ndjson" } });
    }
    if (pathname.startsWith("/v1/receipts/")) {
      const id = pathname.split("/").pop()!;
      if (!receiptIds.includes(id)) return new Response("", { status: 404 });
      return new Response(JSON.stringify({ data: fakeReceipt(id) }), { status: 200 });
    }
    if (pathname.startsWith("/v1/transparency/inclusion/")) {
      const id = pathname.split("/").pop()!;
      if (!receiptIds.includes(id)) return new Response("", { status: 404 });
      return new Response(JSON.stringify({ data: fakeInclusion(id) }), { status: 200 });
    }
    if (pathname.startsWith("/v1/transparency/roots/")) {
      const d = pathname.split("/").pop()!;
      if (d !== opts.rootDate) return new Response("", { status: 404 });
      return new Response(JSON.stringify({ data: fakeRoot }), { status: 200 });
    }
    return new Response("", { status: 404 });
  };

  return { fetchImpl, requests };
}

test("buildArchive writes the expected directory layout", async () => {
  const out = mkdtempSync(path.join(tmpdir(), "acp-archive-"));
  const server = buildStubServer({ receipts: 4, rootDate: "2026-05-14" });
  const counts = await buildArchive({
    baseUrl: "https://acp.test",
    token: "test",
    outDir: out,
    fetchImpl: server.fetchImpl,
  });
  assert.deepEqual(counts, { receipts: 4, inclusion: 4, roots: 1 });
  assert.ok(existsSync(path.join(out, "public_key.pem")));
  assert.equal(readdirSync(path.join(out, "receipts")).length, 4);
  assert.equal(readdirSync(path.join(out, "inclusion")).length, 4);
  assert.equal(readdirSync(path.join(out, "roots")).length, 1);
  // pubkey content is the stub PEM, written verbatim
  assert.match(readFileSync(path.join(out, "public_key.pem"), "utf-8"), /BEGIN PUBLIC KEY/);
});

test("buildArchive is idempotent on second run", async () => {
  const out = mkdtempSync(path.join(tmpdir(), "acp-archive-"));
  const server = buildStubServer({ receipts: 3, rootDate: "2026-05-14" });
  await buildArchive({ baseUrl: "https://acp.test", token: "t", outDir: out, fetchImpl: server.fetchImpl });
  const counts = await buildArchive({ baseUrl: "https://acp.test", token: "t", outDir: out, fetchImpl: server.fetchImpl });
  assert.deepEqual(counts, { receipts: 0, inclusion: 0, roots: 0 });
});

test("buildArchive raises ArchiveError on 401", async () => {
  const out = mkdtempSync(path.join(tmpdir(), "acp-archive-"));
  const fetchImpl: typeof fetch = async () => new Response("", { status: 401 });
  await assert.rejects(
    buildArchive({ baseUrl: "https://acp.test", token: "bad", outDir: out, fetchImpl }),
    (e: Error) => e instanceof ArchiveError && /auth/.test(e.message)
  );
});

test("buildArchive skips inclusion when pending=true", async () => {
  const out = mkdtempSync(path.join(tmpdir(), "acp-archive-"));
  const fetchImpl: typeof fetch = async (input) => {
    const pathname = new URL(typeof input === "string" ? input : input.toString()).pathname;
    if (pathname === "/v1/receipts/key")
      return new Response(JSON.stringify({ public_key_pem: "PEM" }), { status: 200 });
    if (pathname === "/v1/audit/export")
      return new Response('{"id":"a","timestamp":"2026-05-14T10:00:00+00:00"}\n', { status: 200 });
    if (pathname.startsWith("/v1/receipts/"))
      return new Response(JSON.stringify({ data: { receipt: { execution_id: "a" } } }), { status: 200 });
    if (pathname.startsWith("/v1/transparency/inclusion/"))
      return new Response(JSON.stringify({ data: { pending: true, root_date: "2026-05-14" } }), { status: 200 });
    return new Response("", { status: 404 });
  };
  const counts = await buildArchive({ baseUrl: "https://acp.test", token: "t", outDir: out, fetchImpl });
  assert.equal(counts.receipts, 1);
  assert.equal(counts.inclusion, 0);
  assert.equal(counts.roots, 0);
});
