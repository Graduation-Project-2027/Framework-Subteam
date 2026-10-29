/** Plain-language robot status for fleet rows, tags and the inspector. */
import { stateMeta, taskTitle } from "./mission";

/** One line on what the robot is doing, for rows and tags. */
export function robotLine(r) {
  if (!r.adopted) return r.configured ? "Not in the fleet" : "Needs first-time setup";
  if (r.status === "lost") return "Not answering";
  if (!r.stack?.running) return "Stack stopped";
  if (!r.linked) return "Connecting to rosbridge…";
  const m = r.mission || {};
  if (r.hold) return `Holding for ${r.hold.by_label} · ${r.hold.reason}`;
  if (m.task) return `${taskTitle(m.task)} · ${Math.round((m.progress || 0) * 100)}%`;
  if (m.state === "RETURNING") return "Returning to dock";
  return stateMeta(m.state).hint || stateMeta(m.state).label;
}

export function robotTone(r) {
  if (!r.adopted) return "idle";
  if (r.status !== "online" || !r.linked) return r.stack?.running === false ? "idle" : "caution";
  if (r.hold) return "caution";
  return stateMeta(r.mission?.state).tone;
}

export function robotState(r) {
  if (!r.adopted) return r.configured ? "Found" : "New";
  if (r.status === "lost") return "Lost";
  if (!r.stack?.running) return "Stopped";
  if (!r.linked) return "Linking";
  if (r.hold) return "Holding";
  return stateMeta(r.mission?.state).label;
}

export function nextFree(robots, key, base, pad = 2) {
  const used = new Set(robots.map((r) => r.settings?.[key] ?? r[key]).filter((v) => v != null).map(String));
  for (let n = 1; n < 100; n++) {
    const v = base === "" ? String(n + 10) : `${base}${String(n).padStart(pad, "0")}`;
    if (!used.has(v) && !used.has(v.toLowerCase()) && !used.has(v.toUpperCase())) return v;
  }
  return "";
}
