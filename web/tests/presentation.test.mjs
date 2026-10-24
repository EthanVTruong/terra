import assert from "node:assert/strict";
import test from "node:test";
import { clockTime, dayLabel, displayNoteText, evidenceTime, localDay, publicationTime } from "../src/presentation.js";

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


test("historical readings use their persisted publication day and footer while exact source times stay intact", () => {
  const record = Object.freeze({ created_at: "2026-03-11T11:00:00Z", published_at: "2026-10-24T16:02:00Z" });
  assert.equal(publicationTime(record), "2026-10-24T16:02:00Z");
  assert.equal(localDay(publicationTime(record)), "2026-10-24");
  assert.equal(dayLabel(publicationTime(record), "2026-10-24T17:00:00Z"), "Today");
  assert.equal(clockTime(publicationTime(record)), "9:02 AM");
  assert.equal(evidenceTime(record.created_at, publicationTime(record)), "Mar 11 · 4:00 AM");
  assert.equal(record.created_at, "2026-03-11T11:00:00Z");
});

test("only valid timezone-qualified publication clocks replace a legacy creation clock", () => {
  const created_at = "2026-03-11T11:00:00Z";
  for (const published_at of [undefined, null, 42, "", "invalid", "2026-10-24", "2026-10-24T16:02:00", "2026-10-24T16:02:00+99:00"]) {
    assert.equal(publicationTime({ created_at, published_at }), created_at);
  }
  assert.equal(publicationTime({ created_at, published_at: "2026-10-24T09:02:00-07:00" }), "2026-10-24T09:02:00-07:00");
  assert.equal(publicationTime(undefined), undefined);
});

test("notification eligibility compares saved publication with consent, retaining quiet delayed pre-consent notes", () => {
  const enabledAt = Date.parse("2026-10-24T16:01:00Z");
  const created_at = "2026-03-11T11:00:00Z";
  const records = [
    { id: "historical-new", created_at, published_at: "2026-10-24T16:02:00Z" },
    { id: "delayed-before-consent", created_at, published_at: "2026-10-24T16:00:00Z" },
    { id: "exact-consent", created_at, published_at: "2026-10-24T16:01:00Z" },
    { id: "naive-publication", created_at, published_at: "2026-10-24T16:03:00" },
    { id: "legacy-new", created_at: "2026-10-24T16:03:00Z" },
  ];
  const original = structuredClone(records);
  assert.deepEqual(records.filter((record) => Date.parse(publicationTime(record)) > enabledAt).map(({ id }) => id), ["historical-new", "legacy-new"]);
  assert.deepEqual(records, original);
});

test("a publication-ordered mixed feed keeps one Pacific day across replay notes and current conversation", () => {
  const source = "2026-03-11T11:00:00Z";
  const entries = [
    { id: "welcome", created_at: source, published_at: "2026-10-24T16:00:00Z" },
    { id: "question", created_at: "2026-10-24T16:01:00Z" },
    { id: "coverage-limited", created_at: source, published_at: "2026-10-24T16:02:00Z" },
    { id: "answer", created_at: "2026-10-24T16:03:00Z" },
  ];
  assert.deepEqual(entries.map((entry) => localDay(publicationTime(entry))), Array(4).fill("2026-10-24"));
  assert.deepEqual(entries.map((entry) => clockTime(publicationTime(entry))), ["9:00 AM", "9:01 AM", "9:02 AM", "9:03 AM"]);
  assert.equal(entries[0].created_at, source);
  assert.equal(entries[2].created_at, source);
});
