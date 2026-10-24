const farmId = (value) => typeof value === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value);

export function farmNoteTarget(message, fallbackFarmId) {
  const orchardId = Object.hasOwn(message || {}, "orchard_id") ? message.orchard_id : fallbackFarmId;
  if (!farmId(orchardId) || typeof message?.id !== "string" || !message.id || message.id.length > 160) {
    throw new Error("The note’s farm and evidence could not be confirmed.");
  }
  return Object.freeze({ id: message.id, orchard_id: orchardId });
}

export function farmNoteDetailsPath(message, fallbackFarmId) {
  const target = farmNoteTarget(message, fallbackFarmId);
  return `/api/orchards/${encodeURIComponent(target.orchard_id)}/messages/${encodeURIComponent(target.id)}/details`;
}

export function farmFeedPollInterval(replayOnly) {
  return replayOnly === true ? 1200 : 30000;
}

export function showTaraNotification(NotificationType, title, note, { orchardId, onOpen, focusWindow = () => window.focus() } = {}) {
  const target = farmNoteTarget(note);
  if (target.orchard_id !== orchardId) throw new Error("The notification belongs to a different farm feed.");
  const notification = new NotificationType(title, { body: note.text, tag: `terra-${target.id}` });
  notification.onclick = () => {
    try { notification.close(); } catch { /* The browser may have already dismissed it. */ }
    try { focusWindow(); } catch { /* Keep the note usable if window focus is unavailable. */ }
    onOpen?.(target);
  };
  return notification;
}
