import assert from "node:assert/strict";
import test from "node:test";
import { deliveryLabel, demoResultText } from "../src/delivery.js";

test("local publication never becomes phone delivery", () => {
  for (const receipt of [null, { status: "not_requested" }, { status: "published" }]) {
    assert.equal(deliveryLabel(receipt), "Local inbox");
  }
});

test("provider acceptance and sent receipts remain unconfirmed", () => {
  assert.match(deliveryLabel({ status: "provider_accepted" }), /unconfirmed/);
  assert.match(deliveryLabel({ status: "provider_accepted", provider_status: "sent" }), /unconfirmed/);
  for (const state of ["provider_accepted", "queued", "sending", "sent", "uncertain", "failed", "undelivered", "unrecognized"]) {
    assert.notEqual(deliveryLabel({ status: state }), "SMS delivered");
  }
});

test("only a reported delivered state renders as delivered", () => {
  assert.equal(deliveryLabel({ status: "delivered" }), "SMS delivered");
  assert.equal(deliveryLabel({ status: "provider_accepted", provider_status: "delivered" }), "SMS delivered");
});

test("trial receipts identify the delivered content as a placeholder", () => {
  const receipt = { status: "provider_accepted", provider_status: "delivered", content_mode: "trial_template", template_name: "sms_internal_alerts" };
  assert.equal(deliveryLabel(receipt), "Trial placeholder delivered");
  assert.match(demoResultText(receipt), /trial placeholder delivered/);
  assert.match(demoResultText(receipt), /Open Terra for the actual finding/);
  assert.doesNotMatch(demoResultText(receipt), /TERRA DEMO/);
});

test("trial acceptance, uncertainty and local publication never claim a delivered finding", () => {
  for (const state of ["provider_accepted", "queued", "sending", "sent", "uncertain", "failed"]) {
    const receipt = { status: state, content_mode: "trial_template" };
    assert.doesNotMatch(deliveryLabel(receipt), /delivered/);
    assert.doesNotMatch(demoResultText(receipt), /reports.*delivered|labelled TERRA DEMO/);
  }
  assert.equal(deliveryLabel({ status: "published", content_mode: "trial_template" }), "Local inbox");
  assert.match(demoResultText({ status: "delivered" }), /labelled TERRA DEMO/);
});
