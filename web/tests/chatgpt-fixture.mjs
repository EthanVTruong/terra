// Eligible saved account for tests whose subject is the post-login conversation.
export function eligibleChatGPTFixture(path) {
  if (path === "/api/simulation/connection") return { source_context: "controlled-browser-source", connected: false, data_mode: "current", source_expired: false };
  if (path === "/api/chatgpt/status") return { schema_version: "terra-chatgpt-connection-v1", state: "connected", plan_enabled: true,
    active_account_id: "controlled-account", accounts: [{ id: "controlled-account", label: "Controlled account" }],
    writer: { connected: true, active_account_id: "controlled-account", selected_model: "controlled-model", usage_paused: false, cooldown_until: null } };
  if (path === "/api/chatgpt/models") return { models: [{ slug: "controlled-model", display_name: "Controlled model" }], selected_model: "controlled-model" };
  return null;
}
