import { test } from "node:test";
import assert from "node:assert/strict";
import { validatePolicy } from "../src/policy.js";
import { PolicyError } from "../src/errors.js";

test("minimal policy parses", () => {
  const p = validatePolicy({ version: 1, agent: "a1", allow: [{ tool: "search" }] });
  assert.equal(p.agent, "a1");
  assert.equal(p.allow.length, 1);
});

test("unknown top-level key rejected", () => {
  assert.throws(() => validatePolicy({ version: 1, agent: "a", rogue: true }), PolicyError);
});

test("invalid version rejected", () => {
  assert.throws(() => validatePolicy({ version: 99, agent: "a" }), PolicyError);
});

test("missing agent rejected", () => {
  assert.throws(() => validatePolicy({ version: 1 }), PolicyError);
});

test("invalid regex in when rejected", () => {
  assert.throws(
    () =>
      validatePolicy({
        version: 1,
        agent: "a",
        allow: [{ tool: "x", when: { "payload.foo": "([unclosed" } }],
      }),
    PolicyError
  );
});

test("autonomy parsed", () => {
  const p = validatePolicy({
    version: 1,
    agent: "a",
    autonomy: { max_actions_per_minute: 60, require_approval_for: ["send_email"] },
  });
  assert.equal(p.autonomy.maxActionsPerMinute, 60);
  assert.deepEqual(p.autonomy.requireApprovalFor, ["send_email"]);
});
