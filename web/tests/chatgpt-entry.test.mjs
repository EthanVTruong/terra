import assert from "node:assert/strict";
import test from "node:test";
import { recoverExpiredSource } from "../src/chatgpt-entry.js";

test("an expired source explicitly retrieves its local guard then disconnects before a new document", async () => {
  const calls = [], signal = new AbortController().signal;
  const fetcher = async (path, options) => { calls.push({ path, options }); return path === "/api/sms/status"
    ? new Response(JSON.stringify({ control_token: "controlled-local-guard" }), { status: 200 }) : new Response("{}", { status: 200 }); };
  assert.equal(await recoverExpiredSource(fetcher, { kind: "expired", context: "expired-proof" }, signal), true);
  assert.deepEqual(calls.map((call) => call.path), ["/api/sms/status", "/api/simulation/connection"]);
  assert.equal(calls[1].options.method, "DELETE");
  assert.equal(calls[1].options.headers["X-Terra-SMS-Control"], "controlled-local-guard");
  assert.equal(calls[0].options.signal, signal);
  assert.equal(calls[1].options.signal, signal);
});

test("normal sources have no recovery bypass; failed guards and rejected disconnects cannot succeed", async () => {
  let calls = 0;
  await assert.rejects(recoverExpiredSource(async () => { calls++; }, { kind: "live" }), /does not need/);
  assert.equal(calls, 0);
  await assert.rejects(recoverExpiredSource(async () => { calls++; return new Response("{}", { status: 403 }); }, { kind: "expired" }), /confirm this session/);
  assert.equal(calls, 1);
  await assert.rejects(recoverExpiredSource(async (path) => path.endsWith("status") ? new Response(JSON.stringify({ control_token: "guard" })) : new Response("{}", { status: 409 }), { kind: "expired" }), /leave the expired simulation/);
});
