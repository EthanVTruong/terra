// A stale simulation cookie needs an explicit guarded disconnect and a new document.
// The document's immutable source identity is never adopted or replaced in place.
export async function recoverExpiredSource(fetcher, identity, signal) {
  if (identity?.kind !== "expired") throw new Error("This session does not need source recovery.");
  const tokenResponse = await fetcher("/api/sms/status", { cache: "no-store", signal });
  const token = await tokenResponse.json().catch(() => null);
  if (!tokenResponse.ok || typeof token?.control_token !== "string" || !token.control_token) throw new Error("Terra couldn’t confirm this session. Reload and try again.");
  const response = await fetcher("/api/simulation/connection", { method: "DELETE", cache: "no-store", signal,
    headers: { "X-Terra-SMS-Control": token.control_token } });
  if (!response.ok) throw new Error("Terra couldn’t leave the expired simulation. Try again.");
  return true;
}
