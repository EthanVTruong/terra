import assert from "node:assert/strict";
import test from "node:test";
import { farmFeedPollInterval, farmNoteDetailsPath, farmNoteTarget, showTaraNotification } from "../src/notification-return.js";

test("notification clicks close, focus and return to the captured original farm/note", () => {
  const events = [];
  class Notification {
    constructor(title, options) { this.title = title; this.options = options; }
    close() { events.push("close"); }
  }
  const note = { id: "old-note", orchard_id: "farm-a", text: "Weather data is spotty." };
  let selectedFarm = "farm-a";
  const notification = showTaraNotification(Notification, "Farm A", note, {
    orchardId: "farm-a", focusWindow: () => events.push("focus"),
    onOpen: (target) => { selectedFarm = target.orchard_id; events.push(farmNoteDetailsPath(target, "farm-b")); },
  });
  selectedFarm = "farm-b";
  note.orchard_id = "farm-c";
  note.id = "changed-after-publication";
  notification.onclick();
  assert.equal(selectedFarm, "farm-a");
  assert.deepEqual(events, ["close", "focus", "/api/orchards/farm-a/messages/old-note/details"]);
  assert.deepEqual(notification.options, { body: "Weather data is spotty.", tag: "terra-old-note" });
});

test("native note farm wins over current selection; absent farm may use explicit local context", () => {
  assert.equal(farmNoteDetailsPath({ id: "note-a", orchard_id: "farm-a" }, "farm-b"), "/api/orchards/farm-a/messages/note-a/details");
  assert.equal(farmNoteDetailsPath({ id: "answer" }, "farm-b"), "/api/orchards/farm-b/messages/answer/details");
  assert.equal(farmNoteDetailsPath({ id: "note/a?b", orchard_id: "farm-a" }), "/api/orchards/farm-a/messages/note%2Fa%3Fb/details");
  assert.ok(Object.isFrozen(farmNoteTarget({ id: "note", orchard_id: "farm-a" })));
  for (const orchard_id of [null, "", "farm/other", "https://other.invalid"]) {
    assert.throws(() => farmNoteTarget({ id: "note", orchard_id }, "farm-b"));
  }
});

test("cross-farm or incomplete feed notifications never construct an OS notification", () => {
  let constructions = 0;
  class Notification { constructor() { constructions += 1; } }
  for (const note of [{ id: "note", orchard_id: "farm-b" }, { id: "note" }, { orchard_id: "farm-a" }]) {
    assert.throws(() => showTaraNotification(Notification, "Farm A", note, { orchardId: "farm-a" }));
  }
  assert.equal(constructions, 0);
});

test("dismissal or focus failures do not lose the exact note return action", () => {
  class Notification { close() { throw new Error("already closed"); } }
  let opened;
  const notification = showTaraNotification(Notification, "Farm", { id: "note", orchard_id: "farm-a" }, {
    orchardId: "farm-a", focusWindow: () => { throw new Error("focus unavailable"); }, onOpen: (target) => { opened = target; },
  });
  notification.onclick();
  assert.deepEqual(opened, { id: "note", orchard_id: "farm-a" });
});

test("only the explicit server replay flag enables fast feed polling", () => {
  assert.equal(farmFeedPollInterval(true), 1200);
  for (const value of [undefined, null, false, "true", "injected", 1, {}]) assert.equal(farmFeedPollInterval(value), 30000);
});
