// Bind each document to the source it opened. A cookie changed by another tab
// must never silently redirect a delayed location callback or chat submission.
export function createSourceTransport(nativeFetch, origin) {
  let initialization;
  const initialize = () => initialization ||= (async () => {
    try {
      const status = await nativeFetch("/api/simulation/connection", { cache: "no-store" });
      if (status.ok) {
        const value = await status.json();
        if (typeof value.source_context === "string" && value.source_context) return { context: value.source_context };
      } else if (status.status === 404) {
        // The standalone replay server deliberately has no live-source router.
        const response = await nativeFetch("/api/context", { cache: "no-store" });
        if (response.ok && (await response.json()).replay_only === true) return { isolated: true };
      }
    } catch { /* Reads remain available; mutations need a confirmed source. */ }
    return {};
  })();
  async function fetch(input, options = {}) {
    const url = new URL(typeof input === "string" ? input : input.url, origin);
    if (url.origin !== origin || !url.pathname.startsWith("/api/")) return nativeFetch(input, options);
    const source = await initialize();
    const method = (options.method || input?.method || "GET").toUpperCase();
    if (!source.context && !source.isolated && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      throw new Error("The chat source could not be confirmed. Reload Terra before making changes.");
    }
    const headers = new Headers(options.headers || input?.headers);
    if (source.context) headers.set("X-Terra-Source-Context", source.context);
    const response = await nativeFetch(input, { ...options, headers });
    if (response.status === 409) {
      const body = await response.clone().json().catch(() => null);
      if (["source_context_mismatch", "simulation_context_expired"].includes(body?.error?.code)) {
        throw new Error("This chat’s source changed in another tab or after a restart. Reload Terra to continue.");
      }
    }
    return response;
  }
  return { initialize, fetch };
}

const source = createSourceTransport((...args) => globalThis.fetch(...args), globalThis.location?.origin || "http://localhost");
export const initializeSource = source.initialize;
export const sourceFetch = source.fetch;
