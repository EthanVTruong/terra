import assert from "node:assert/strict";
import test from "node:test";
import { deliveryLabel } from "../src/delivery.js";

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
