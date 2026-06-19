/**
 * SystemLauncher — header controls to bring the robot stack up/down.
 * Talks to launcher_server.py (stdlib HTTP) over plain fetch, so it works
 * even when the ROS stack (and rosbridge) is down.
 */
import { useState, useEffect, useCallback } from "react";

const BASE = `http://${window.location.hostname}:8088`;

export default function SystemLauncher() {
  const [status, setStatus] = useState({ running: false, target: null });
  const [reachable, setReachable] = useState(true);
  const [busy, setBusy] = useState(false);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${BASE}/api/status`, { cache: "no-store" });
      const j = await r.json();
      setStatus(j);
      setReachable(true);
    } catch {
      setReachable(false);
    }
  }, []);

  useEffect(() => {
    poll();
    const id = setInterval(poll, 2000);
    return () => clearInterval(id);
  }, [poll]);

  const launch = async (target) => {
    setBusy(true);
    try {
      await fetch(`${BASE}/api/launch?target=${target}`, { method: "POST" });
    } catch { /* ignore */ }
    await poll();
    setBusy(false);
  };

  const stop = async () => {
    if (!window.confirm("Stop the running system?")) return;
    setBusy(true);
    try { await fetch(`${BASE}/api/stop`, { method: "POST" }); } catch { /* ignore */ }
    await poll();
    setBusy(false);
  };

  if (!reachable) {
    return (
      <div className="launcher launcher--offline" title="Start launcher_server.py to enable system launch">
        <span className="launcher__dot" /> Launcher offline
      </div>
    );
  }

  return (
    <div className="launcher">
      {status.running ? (
        <>
          <span className="launcher__chip launcher__chip--on">
            <span className="launcher__dot launcher__dot--on" />
            {status.target === "real" ? "Real robot" : "Simulation"} running
          </span>
          <button className="btn btn--danger btn--sm" onClick={stop} disabled={busy}>
            ■ Stop System
          </button>
        </>
      ) : (
        <>
          <button className="btn btn--success btn--sm" onClick={() => launch("sim")} disabled={busy}>
            ▶ Launch Sim
          </button>
          <button className="btn btn--sm" onClick={() => launch("real")} disabled={busy}>
            🤖 Launch Real
          </button>
        </>
      )}
    </div>
  );
}
