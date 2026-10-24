const states = {
  disconnected: { kind: "disconnected", label: "Not connected", copy: "Use your verified demo phone for text alerts.", action: "Connect phone" },
  checking: { kind: "checking", label: "Connecting…", copy: "Checking text delivery.", action: "Connecting…" },
  setup: { kind: "setup", label: "Setup required", copy: "Text delivery still needs setup on this server.", action: "Check again" },
  connected: { kind: "connected", label: "Ready", copy: "", action: "Refresh connection" },
  paused: { kind: "paused", label: "Sending paused", copy: "Your phone stays configured. New texts are paused.", action: "Check connection" },
  unavailable: { kind: "unavailable", label: "Unavailable", copy: "Can’t reach phone alerts right now.", action: "Try again" },
};

export function phoneConnectionState(status, { checking = false, attempted = false, unavailable = false } = {}) {
  if (checking || (!status && !unavailable)) return states.checking;
  if (unavailable) return states.unavailable;
  if (status.configured !== true) return attempted ? states.setup : states.disconnected;
  if (status.suppressed === true) return states.paused;
  if (status.enabled !== true || typeof status.recipient !== "string" || !status.recipient || status.configuration_errors?.length) return states.setup;
  return states.connected;
}
