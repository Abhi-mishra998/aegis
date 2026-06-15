// Sprint 8 — VS Code extension smoke test.
//
// Runs outside the VS Code host: just instantiates the pure HTTP client
// and asserts the URL/header composition. The test is invoked from
// scripts/dev/vscode_extension_smoke.sh, which compiles the extension
// and then `node` runs `out/smoke.js`.

import { AegisClient } from "./aegisClient";

function assert(cond: boolean, msg: string): void {
  if (!cond) {
    throw new Error(`assertion failed: ${msg}`);
  }
}

function main(): void {
  const c = new AegisClient("https://dev.aegisagent.in/", "k-test-1");

  // buildUrl strips the trailing slash on the gateway and normalises the
  // leading slash on the path.
  assert(
    c.buildUrl("/audit/logs") === "https://dev.aegisagent.in/audit/logs",
    "buildUrl absolute",
  );
  assert(
    c.buildUrl("audit/logs") === "https://dev.aegisagent.in/audit/logs",
    "buildUrl prepends slash",
  );

  // Headers carry the key but NOT the tenant-id before validateKey() is
  // called — VS Code MUST never spoof a tenant for an unvalidated key.
  const pre = c.buildHeaders();
  assert(pre["X-API-Key"] === "k-test-1", "X-API-Key set");
  assert(!("X-Tenant-ID" in pre), "no tenant before validateKey()");

  // Manually inject a tenant id to mimic post-validation state.
  (c as unknown as { tenantId: string }).tenantId =
    "00000000-0000-0000-0000-000000000001";
  const post = c.buildHeaders({ "X-Aegis-Hint": "from-smoke" });
  assert(
    post["X-Tenant-ID"] === "00000000-0000-0000-0000-000000000001",
    "tenant id propagated",
  );
  assert(post["X-Aegis-Hint"] === "from-smoke", "extra headers merged");

  // Final sanity — Content-Type stays json so POSTs don't drop bodies.
  assert(post["Content-Type"] === "application/json", "json content type");

  // eslint-disable-next-line no-console
  console.log("aegis-vscode-smoke: OK");
}

main();
