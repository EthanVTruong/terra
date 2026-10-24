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

test("trial sender error identifies the assigned sender without recommending an upgrade", () => {
  for (const error_code of [572003, "572003"]) {
    const text = demoResultText({ status: "failed", error_code, content_mode: "trial_template", message: "private provider response" });
    assert.match(text, /TWILIO_FROM_NUMBER/);
    assert.match(text, /assigned trial sender.*verified recipient.*Twilio Console/);
    assert.match(text, /prepare a new demo/);
    assert.doesNotMatch(text, /upgrade|purchase|private provider response/);
  }
});

test("receipt refresh failures take precedence over trial sender guidance", () => {
  const text = demoResultText({ status: "failed", error_code: "572003", receipt_error: "provider_receipt_unavailable" });
  assert.match(text, /Delivery status could not be refreshed/);
  assert.match(text, /Checking again will not resend/);
  assert.doesNotMatch(text, /TWILIO_FROM_NUMBER|prepare a new demo/);
});
