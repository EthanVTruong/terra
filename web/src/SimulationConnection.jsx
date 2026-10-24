import { sourceFetch as fetch, sourceIdentity } from "./source-fetch";
import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

export default function SimulationConnection({ orchardId, locked, onBusy }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef(false), alive = useRef(true);
  const connected = ["simulation", "isolated"].includes(sourceIdentity().kind);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  async function openSimulation() {
    if (pending.current || (!connected && locked)) return;
    setError("");
    if (connected) {
      const station = window.open("/stations", "terra-station-lab");
      if (station) station.opener = null;
      else setError("Allow the Station lab window to open, then try again.");
      return;
    }
    pending.current = true; setBusy(true); onBusy(true); setError("");
    // Reserve the tab during the click so browsers do not block the station lab
    // when connection initialization takes longer than the user activation.
    const station = window.open("about:blank", "terra-station-lab");
    if (station) station.opener = null;
    const controller = new AbortController(), deadline = setTimeout(() => controller.abort(), 60000);
    try {
      if (!station) throw new Error("Allow the Station lab window to open, then try again.");
      const status = await fetch("/api/sms/status", { signal: controller.signal, cache: "no-store" });
      const guard = status.ok ? await status.json() : null;
      if (!guard?.control_token) throw new Error("Reconnect to the local Terra service and try again.");
      const response = await fetch("/api/simulation/connect", {
        method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": guard.control_token },
        body: JSON.stringify({ orchard_id: orchardId || null }),
      });
      if (!response.ok) throw new Error("The simulator couldn’t connect. Try again from the local Terra server.");
      const result = await response.json();
      if (result.connected !== true) throw new Error("The connection couldn’t be confirmed. Try again.");
      try {
        localStorage.setItem("terra.simulation.liveFarm", orchardId || "");
      } catch { /* Server setup still restores the live selection when storage is unavailable. */ }
      station.location.replace("/stations");
      window.location.reload();
    } catch (reason) {
      station?.close();
      if (alive.current) setError(reason.name === "AbortError" ? "Connecting took too long. Try again." : reason.message);
    } finally { clearTimeout(deadline); pending.current = false; onBusy(false); if (alive.current) setBusy(false); }
  }

  return <div className="simulation-connection">
    <button className="open-file simulation-open" type="button" disabled={busy || (!connected && locked)} onClick={openSimulation}><Icon name="panel" size={16} /><span>{busy ? "Opening simulation…" : "Open simulation"}</span><Icon name="chevron" size={12} /></button>
    {error && <p className="query-error" role="alert">{error}</p>}
  </div>;
}
