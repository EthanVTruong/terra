import assert from "node:assert/strict";
import test from "node:test";
import { clockTime, dayLabel, evidenceTime, localDay } from "../src/presentation.js";

test("notes group at Pacific midnight rather than UTC midnight", () => {
  assert.equal(localDay("2026-10-24T06:59:00Z"), "2026-10-23");
  assert.equal(localDay("2026-10-24T07:00:00Z"), "2026-10-24");
  assert.equal(dayLabel("2026-10-24T06:59:00Z", "2026-10-24T07:00:00Z"), "Yesterday");
  assert.equal(dayLabel("2026-10-24T07:00:00Z", "2026-10-24T07:00:00Z"), "Today");
});

test("calendar grouping survives the spring and fall daylight saving changes", () => {
  assert.equal(dayLabel("2026-03-08T08:30:00Z", "2026-03-09T07:30:00Z"), "Yesterday");
  assert.equal(localDay("2026-11-01T08:30:00Z"), localDay("2026-11-01T09:30:00Z"));
  assert.equal(clockTime("2026-11-01T08:30:00Z"), "1:30 AM");
  assert.equal(clockTime("2026-11-01T09:30:00Z"), "1:30 AM");
});

test("evidence time keeps the date when readings are from another day or year", () => {
  const note = "2026-10-24T15:07:00Z";
  assert.equal(evidenceTime("2026-10-24T08:00:00Z", note), "1:00 AM");
  assert.equal(evidenceTime("2026-10-24T06:00:00Z", note), "Oct 23 · 11:00 PM");
  assert.equal(evidenceTime("2025-10-24T08:00:00Z", note), "Oct 24, 2025 · 1:00 AM");
  assert.match(dayLabel("2025-10-24T08:00:00Z", note), /2025/);
});

test("missing timestamps never look like a real reading time", () => {
  for (const value of [null, undefined, "", "invalid"]) {
    assert.equal(clockTime(value), "Not supplied");
    assert.equal(evidenceTime(value), "Not supplied");
    assert.equal(localDay(value), "");
  }
});
