export const stationName = (id) => ({ 6: "Davis", 226: "Woodland", 139: "Winters", 212: "Hastings Tract East", 235: "Verona" }[id] || `Station ${id}`);
export const temperature = (value) => Number.isFinite(value) ? `${value.toFixed(1)}°F` : "Unavailable";
export const departure = (value) => Number.isFinite(value) ? `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}°F` : "Unavailable";
export const timestamp = (value) => {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not supplied";
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(value));
};
const pacific = "America/Los_Angeles";
const dayFormatter = new Intl.DateTimeFormat("en-US", { timeZone: pacific, year: "numeric", month: "2-digit", day: "2-digit" });
const clockFormatter = new Intl.DateTimeFormat("en-US", { timeZone: pacific, hour: "numeric", minute: "2-digit" });
function dateValue(value) {
  if (value == null || value === "") return null;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date : null;
}
export function clockTime(value) {
  const date = dateValue(value);
  return date ? clockFormatter.format(date) : "Not supplied";
}
export function localDay(value) {
  const date = dateValue(value);
  if (!date) return "";
  const parts = Object.fromEntries(dayFormatter.formatToParts(date).map(({ type, value }) => [type, value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
export function dayLabel(value, now = Date.now()) {
  const day = localDay(value), today = localDay(now);
  if (!day) return "Date unavailable";
  if (day === today) return "Today";
  const [year, month, date] = today.split("-").map(Number);
  const yesterday = new Date(Date.UTC(year, month - 1, date - 1)).toISOString().slice(0, 10);
  if (day === yesterday) return "Yesterday";
  return new Intl.DateTimeFormat("en-US", { timeZone: pacific, weekday: "short", month: "short", day: "numeric", ...(day.slice(0, 4) !== today.slice(0, 4) ? { year: "numeric" } : {}) }).format(dateValue(value));
}
export function evidenceTime(value, contextTime) {
  const date = dateValue(value);
  if (!date) return "Not supplied";
  const day = localDay(value), contextDay = localDay(contextTime);
  if (day === contextDay) return clockTime(value);
  const dateLabel = new Intl.DateTimeFormat("en-US", { timeZone: pacific, month: "short", day: "numeric", ...(day.slice(0, 4) !== (contextDay || localDay(Date.now())).slice(0, 4) ? { year: "numeric" } : {}) }).format(date);
  return `${dateLabel} · ${clockTime(value)}`;
}
export function dataModeLabel(packet, sourceKind) {
  if (packet.data_mode === "injected") return sourceKind === "example" ? "Historical · injected fault" : "Injected fault · demo";
  if (packet.data_mode !== "current") return packet.data_mode.replaceAll("_", " ");
  if (sourceKind === "monitor") return "Current weather";
  if (sourceKind !== "query") return "Saved current-weather query";
  const source = packet.provenance.live_source;
  return source ? `Current weather · ${source.status === "cached_after_error" ? "saved after error" : source.status}` : "Current weather";
}

export function displayNoteText(message, orchard) {
  let text = message.text;
  const prefixes = ["Demo orchard: ", "Demo replay: ", orchard?.name && `${orchard.name}: `].filter(Boolean);
  for (let count = 0; count < 3; count += 1) {
    const prefix = prefixes.find((value) => text.startsWith(value));
    if (!prefix) break;
    text = text.slice(prefix.length);
  }
  if (text.startsWith("monitoring is connected; ")) text = text.slice("monitoring is connected; ".length);
  text = text.replace(/\s+As of [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2} (?:AM|PM) (?:PST|PDT)(?:\.|; related updates are under Details\.)$/, "");
  return text ? text[0].toUpperCase() + text.slice(1) : message.text;
}
