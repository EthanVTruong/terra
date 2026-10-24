import test from "node:test";
import assert from "node:assert/strict";
import { createSourceTransport } from "../src/source-fetch.js";

const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("a delayed request retains its original document source after another tab switches", async () => {
  let cookieSource = "live-A";
  const calls = [];
  const transport = createSourceTransport(async (path, options) => {
    if (path === "/api/simulation/connection") return json({ source_context: cookieSource });
    calls.push(options.headers.get("X-Terra-Source-Context"));
    return options.headers.get("X-Terra-Source-Context") === cookieSource ? json({}) : json({ error: { code: "source_context_mismatch" } }, 409);
  }, "http://localhost");
  await transport.initialize();
  cookieSource = "replay-B";
  await assert.rejects(transport.fetch("/api/tara/setup", { method: "POST" }), /source changed/);
  assert.deepEqual(calls, ["live-A"]);
  await assert.rejects(transport.fetch("/api/tara", { method: "POST" }), /source changed/);
  assert.deepEqual(calls, ["live-A", "live-A"]);
});

test("unconfirmed runtime blocks writes without sending the mutation", async () => {
  const calls = [];
  const transport = createSourceTransport(async (path) => { calls.push(path); return json({}, 503); }, "http://localhost");
  await transport.initialize();
  await assert.rejects(transport.fetch("/api/orchards", { method: "POST" }), /could not be confirmed/);
  assert.deepEqual(calls, ["/api/simulation/connection"]);
});

test("only explicit standalone replay context permits the endpoint-free replay server", async () => {
  for (const replayOnly of [true, false]) {
    let writes = 0;
    const transport = createSourceTransport(async (path) => {
      if (path === "/api/simulation/connection") return json({}, 404);
      if (path === "/api/context") return json({ replay_only: replayOnly });
      writes++; return json({});
    }, "http://localhost");
    await transport.initialize();
    if (replayOnly) await transport.fetch("/api/demo/action", { method: "POST" });
    else await assert.rejects(transport.fetch("/api/demo/action", { method: "POST" }));
    assert.equal(writes, replayOnly ? 1 : 0);
  }
});

test("simulator expiry remains explicit and does not adopt a new source", async () => {
  const transport = createSourceTransport(async (path, options) => path === "/api/simulation/connection" ? json({ source_context: "expired-token", source_expired: true }) : json({ error: { code: "simulation_context_expired" } }, 409), "http://localhost");
  await transport.initialize();
  await assert.rejects(transport.fetch("/api/tara"), /restart/);
});
