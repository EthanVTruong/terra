import assert from "node:assert/strict";
import test from "node:test";
import { clockTime, dayLabel, displayNoteText, evidenceTime, localDay } from "../src/presentation.js";

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

test("display removes generated time suffixes while retaining the exact stored message", () => {
  const text = "Demo orchard: Demo orchard: weather coverage is limited. As of Oct 24, 1:02 AM PST.";
  const message = { text };
  assert.equal(displayNoteText(message, { name: "Demo orchard" }), "Weather coverage is limited.");
  assert.equal(message.text, text);
  assert.equal(displayNoteText({ text: "North field: estimated chill is 120 hours. As of Nov 3, 2:00 PM PDT." }, { name: "North field" }), "Estimated chill is 120 hours.");
  assert.equal(displayNoteText({ text: "Demo replay: North field: weather support is limited. As of Jan 11, 1:00 AM PST; related updates are under Details." }, { name: "North field" }), "Weather support is limited.");
});

test("meaningful dates within the message body are preserved", () => {
  const text = "As of November 1, chill accumulation is 120 hours.";
  assert.equal(displayNoteText({ text }), text);
  const datedObservation = "Weather coverage as of Oct 24, 1:00 AM PST is incomplete.";
  assert.equal(displayNoteText({ text: datedObservation }), datedObservation);
});
