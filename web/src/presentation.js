export const stationName = (id) => ({ 6: "Davis", 226: "Woodland", 139: "Winters", 212: "Hastings Tract East", 235: "Verona" }[id] || `Station ${id}`);
export const temperature = (value) => Number.isFinite(value) ? `${value.toFixed(1)}°F` : "Unavailable";
export const departure = (value) => Number.isFinite(value) ? `${value < 0 ? "−" : "+"}${Math.abs(value).toFixed(1)}°F` : "Unavailable";
export const timestamp = (value) => {
  if (!value || !Number.isFinite(Date.parse(value))) return "Not supplied";
  return new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }).format(new Date(value));
};
export const clockTime = (value) => new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", minute: "2-digit" }).format(new Date(value));
export function dataModeLabel(packet, sourceKind) {
  if (packet.data_mode === "injected") return sourceKind === "example" ? "Historical · injected fault" : "Injected fault · demo";
  if (packet.data_mode !== "current") return packet.data_mode.replaceAll("_", " ");
  if (sourceKind !== "query") return "Saved current-weather query";
  const source = packet.provenance.live_source;
  return source ? `Current weather · ${source.status === "cached_after_error" ? "saved after error" : source.status}` : "Current weather";
}
