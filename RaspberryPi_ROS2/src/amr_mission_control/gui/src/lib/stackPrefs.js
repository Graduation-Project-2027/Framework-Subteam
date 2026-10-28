import { useState } from "react";

const KEY = "leverage.stack.v1";

/** Launch preferences: real robot or simulation, open RViz. */
export function useStackPrefs() {
  const [prefs, setPrefs] = useState(() => {
    try { return { target: "real", rviz: false, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { target: "real", rviz: false }; }
  });
  const update = (patch) => setPrefs((p) => {
    const next = { ...p, ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  });
  return [prefs, update];
}

export function describeStack(status) {
  if (!status?.running) return "Stopped";
  const where = status.target === "sim" ? "simulation" : "real robot";
  return status.mode === "mapping" ? `Mapping, ${where}` : `Navigation on ${status.map}, ${where}`;
}

const rtf = typeof Intl !== "undefined" ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;
export function ago(epochSeconds) {
  if (!epochSeconds || !rtf) return "";
  const s = epochSeconds - Date.now() / 1000;
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(Math.round(s), "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}
