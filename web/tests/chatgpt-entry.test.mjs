import assert from "node:assert/strict";
import test from "node:test";
import { recoverExpiredSource } from "../src/chatgpt-entry.js";

function savedSelection(liveFarm = "original-live-farm") {
  const values = new Map([
    ["terra.tara.activeFarm", "farm-only-in-expired-simulation"],
    ["terra.tara.v1.original-live-farm.draft", '"keep the live draft"'],
    ["terra.tara.v1.farm-only-in-expired-simulation.draft", '"keep the simulation draft"'],
  ]);
  if (liveFarm !== undefined) values.set("terra.simulation.liveFarm", liveFarm);
  return { values, getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

test("an expired source explicitly retrieves its local guard then disconnects before a new document", async () => {
  const calls = [], signal = new AbortController().signal;
  const selection = savedSelection(), before = [...selection.values];
  const identity = Object.freeze({ kind: "expired", context: "expired-proof" });
  const fetcher = async (path, options) => {
    calls.push({ path, options });
    assert.deepEqual([...selection.values], before, "Selection changes only after the guarded disconnect is confirmed");
    return path === "/api/sms/status" ? new Response(JSON.stringify({ control_token: "controlled-local-guard" }), { status: 200 })
      : new Response(JSON.stringify({ connected: false, source_expired: false, source_context: "new-live-context" }), { status: 200 });
  };
  assert.equal(await recoverExpiredSource(fetcher, identity, signal, selection), true);
  assert.deepEqual(calls.map((call) => call.path), ["/api/sms/status", "/api/simulation/connection"]);
  assert.equal(calls[1].options.method, "DELETE");
  assert.equal(calls[1].options.headers["X-Terra-SMS-Control"], "controlled-local-guard");
  assert.equal(calls[0].options.signal, signal);
  assert.equal(calls[1].options.signal, signal);
  assert.equal(selection.getItem("terra.tara.activeFarm"), "original-live-farm");
  assert.equal(selection.getItem("terra.simulation.liveFarm"), null);
  assert.equal(selection.getItem("terra.tara.v1.original-live-farm.draft"), '"keep the live draft"');
  assert.equal(selection.getItem("terra.tara.v1.farm-only-in-expired-simulation.draft"), '"keep the simulation draft"');
  assert.equal(identity.context, "expired-proof", "A new document must adopt the new source context");
});

test("normal sources have no recovery bypass; failed guards and rejected disconnects cannot succeed", async () => {
  let calls = 0;
  const selection = savedSelection(), before = [...selection.values];
  await assert.rejects(recoverExpiredSource(async () => { calls++; }, { kind: "live" }, undefined, selection), /does not need/);
  assert.equal(calls, 0);
  await assert.rejects(recoverExpiredSource(async () => { calls++; return new Response("{}", { status: 403 }); }, { kind: "expired" }, undefined, selection), /confirm this session/);
  assert.equal(calls, 1);
  for (const [code, result] of [[409, {}], [200, {}], [200, { connected: true, source_expired: false }], [200, { connected: false, source_expired: true }]]) {
    await assert.rejects(recoverExpiredSource(async (path) => path.endsWith("status") ? new Response(JSON.stringify({ control_token: "guard" })) : new Response(JSON.stringify(result), { status: code }), { kind: "expired" }, undefined, selection), /leave the expired simulation/);
    assert.deepEqual([...selection.values], before);
  }
});

test("an empty or absent original live selection clears only the simulation selection", async () => {
  for (const liveFarm of ["", null]) {
    const selection = savedSelection(liveFarm);
    if (liveFarm === null) selection.removeItem("terra.simulation.liveFarm");
    const fetcher = async (path) => new Response(JSON.stringify(path.endsWith("status") ? { control_token: "guard" } : { connected: false, source_expired: false }));
    assert.equal(await recoverExpiredSource(fetcher, { kind: "expired" }, undefined, selection), true);
    assert.equal(selection.getItem("terra.tara.activeFarm"), null);
    assert.equal(selection.getItem("terra.simulation.liveFarm"), null);
    assert.equal(selection.values.size, 2, "Both saved drafts remain untouched");
  }
});
