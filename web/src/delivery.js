export function deliveryLabel(receipt) {
  const state = receipt?.provider_status || receipt?.status || "not_requested";
  return ({
    not_requested: "Local inbox", published: "Local inbox",
    provider_accepted: "Accepted · unconfirmed", accepted: "Accepted · unconfirmed",
    queued: "SMS queued", sending: "SMS sending", sent: "Sent · unconfirmed",
    delivered: "SMS delivered", failed: "SMS failed", undelivered: "SMS undelivered",
    uncertain: "Delivery uncertain", suppressed: "SMS paused", disabled: "SMS disabled",
    expired: "SMS expired", retry_pending: "Retry pending", rejected: "SMS rejected",
    canceled: "SMS canceled", rate_limited: "SMS rate limited",
  })[state] || "Delivery status unavailable";
}
