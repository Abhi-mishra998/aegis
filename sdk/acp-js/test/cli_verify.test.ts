import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const BUNDLE_PATH = "/tmp/cross-cli-bundle";

function runCli(...args: string[]) {
  const proc = spawnSync(
    process.execPath,
    ["--import", "tsx/esm", "src/cli.ts", ...args],
    { cwd: new URL("..", import.meta.url).pathname, encoding: "utf-8" }
  );
  return { code: proc.status, stdout: proc.stdout, stderr: proc.stderr };
}

test("TS CLI verifies a Python-built bundle", () => {
  if (!existsSync(BUNDLE_PATH)) {
    // The bundle is built ad-hoc by the cross-language smoke script. Skip if
    // it isn't present (CI runs the Python build first).
    return;
  }
  const r = runCli("verify-bundle", BUNDLE_PATH, "--json");
  assert.equal(r.code, 0, `stderr: ${r.stderr}\nstdout: ${r.stdout}`);
  const payload = JSON.parse(r.stdout);
  assert.equal(payload.ok, true);
  assert.ok(payload.counts.receipts_ok >= 1, "must have verified receipts");
  assert.equal(payload.counts.inclusion_ok, payload.counts.inclusion_checked);
});

test("TS CLI usage prints when called with no args", () => {
  const r = runCli();
  assert.equal(r.code, 2);
  assert.match(r.stderr, /usage/);
});

test("TS CLI version", () => {
  const r = runCli("version");
  assert.equal(r.code, 0);
  assert.match(r.stdout, /^acp 0\.2\.0/);
});
