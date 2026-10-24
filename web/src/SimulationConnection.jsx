import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

export default function SimulationConnection({ orchardId, locked, onBusy }) {
  const [connected, setConnected] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const pending = useRef(false), alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    fetch("/api/simulation/connection", { signal: controller.signal, cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value) => { if (alive.current && value) setConnected(value.connected === true); })
      .catch(() => {});
    return () => { alive.current = false; controller.abort(); };
  }, []);

  async function changeConnection(disconnect = false) {
    if (pending.current || locked) return;
    pending.current = true; setBusy(true); onBusy(true); setError("");
    // Reserve the tab during the click so browsers do not block the station lab
    // when connection initialization takes longer than the user activation.
    const station = disconnect ? null : window.open("about:blank", "terra-station-lab");
    if (station) station.opener = null;
    const controller = new AbortController(), deadline = setTimeout(() => controller.abort(), 60000);
    try {
      const status = await fetch("/api/sms/status", { signal: controller.signal, cache: "no-store" });
      const guard = status.ok ? await status.json() : null;
      if (!guard?.control_token) throw new Error("Reconnect to the local Terra service and try again.");
      const response = await fetch(disconnect ? "/api/simulation/connection" : "/api/simulation/connect", {
        method: disconnect ? "DELETE" : "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json", "X-Terra-SMS-Control": guard.control_token },
        ...(disconnect ? {} : { body: JSON.stringify({ orchard_id: orchardId || null }) }),
      });
      if (!response.ok) throw new Error("The simulator couldn’t connect. Try again from the local Terra server.");
      const result = await response.json();
      if (result.connected !== !disconnect) throw new Error("The connection couldn’t be confirmed. Try again.");
      try {
        if (disconnect) {
          const liveFarm = localStorage.getItem("terra.simulation.liveFarm");
          if (liveFarm) localStorage.setItem("terra.tara.activeFarm", liveFarm);
          else localStorage.removeItem("terra.tara.activeFarm");
          localStorage.removeItem("terra.simulation.liveFarm");
        } else localStorage.setItem("terra.simulation.liveFarm", orchardId || "");
      } catch { /* Server setup still restores the live selection when storage is unavailable. */ }
      if (!disconnect && station) station.location.replace("/stations");
      window.location.reload();
    } catch (reason) {
      station?.close();
      if (alive.current) setError(reason.name === "AbortError" ? "Connecting took too long. Try again." : reason.message);
    } finally { clearTimeout(deadline); pending.current = false; onBusy(false); if (alive.current) setBusy(false); }
  }

  return <div className="simulation-connection">
    {connected ? <>
      <a className="open-file simulation-open" href="/stations" target="terra-station-lab" rel="noopener"><Icon name="panel" size={16} /><span>Open Station lab</span><span className="simulation-dot" aria-label="Simulator connected" /></a>
      <button className="tara-text-button simulation-disconnect" type="button" disabled={busy || locked} onClick={() => changeConnection(true)}>{busy ? "Disconnecting…" : "Disconnect simulator"}</button>
    </> : <button className="open-file simulation-open" type="button" disabled={busy || locked} onClick={() => changeConnection()}><Icon name="panel" size={16} /><span>{busy ? "Connecting simulator…" : "Connect simulator"}</span><Icon name="chevron" size={12} /></button>}
    <p className="simulation-description">{connected ? "Chat is using Station lab readings." : "Use Station lab readings in this chat."}</p>
    {error && <p className="query-error" role="alert">{error}</p>}
  </div>;
}
