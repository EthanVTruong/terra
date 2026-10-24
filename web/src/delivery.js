export function deliveryLabel(receipt) {
  const state = receipt?.provider_status || receipt?.status || "not_requested";
  const placeholder = receipt?.content_mode === "trial_template";
  const prefix = placeholder ? "Trial placeholder" : "SMS";
  return ({
    not_requested: "Local inbox", published: "Local inbox",
    provider_accepted: placeholder ? "Trial placeholder accepted · unconfirmed" : "Accepted · unconfirmed",
    accepted: placeholder ? "Trial placeholder accepted · unconfirmed" : "Accepted · unconfirmed",
    queued: `${prefix} queued`, sending: `${prefix} sending`,
    sent: placeholder ? "Trial placeholder sent · unconfirmed" : "Sent · unconfirmed",
    delivered: `${prefix} delivered`, failed: `${prefix} failed`, undelivered: `${prefix} undelivered`,
    uncertain: placeholder ? "Trial placeholder delivery uncertain" : "Delivery uncertain",
    suppressed: `${prefix} paused`, disabled: `${prefix} disabled`,
    expired: `${prefix} expired`, retry_pending: "Retry pending", rejected: `${prefix} rejected`,
    canceled: `${prefix} canceled`, rate_limited: `${prefix} rate limited`,
  })[state] || "Delivery status unavailable";
}

export function demoResultText(result) {
  const status = result.provider_status || result.status;
  if (result.receipt_error) return `Delivery status could not be refreshed (${result.receipt_error}). Last reported state: ${String(status || "unknown").replaceAll("_", " ")}. Checking again will not resend.`;
  if (status === "delivered") return result.content_mode === "trial_template"
    ? "Twilio reports the trial placeholder delivered. Open Terra for the actual finding."
    : "Twilio reports delivered. Check your phone for the message labelled TERRA DEMO.";
  if (["provider_accepted", "queued", "accepted", "sending", "sent"].includes(status)) return `${result.content_mode === "trial_template" ? "Trial placeholder" : "Twilio"}: ${status}. Phone delivery is still unconfirmed.`;
  if (status === "uncertain") return "The send outcome is uncertain. Check Twilio’s message log before sending a new demo.";
  if (["failed", "undelivered", "rejected"].includes(status)) return `Message ${status}${result.error_code ? ` (${result.error_code})` : ""}. Check your Twilio sender and account readiness.`;
  return `SMS: ${String(status || "unavailable").replaceAll("_", " ")}${result.error_code ? ` (${result.error_code})` : ""}.`;
}
