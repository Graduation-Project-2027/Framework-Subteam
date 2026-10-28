export const DEFAULT_STATE = {
  state: "IDLE",
  robot_pose: { x: 0, y: 0, yaw: 0 },
  current_task: null,
  current_step: "",
  progress: 0,
  carrying: false,
  speed: 0.4,
  queue: [],
  locations: { dock: { x: 0, y: 0, yaw: 0, name: "Dock" }, racks: [], drop_zones: [], lanes: [] },
};

/** Merge a raw /mission_control/state payload over safe defaults. */
export function normalizeState(data) {
  if (!data || typeof data !== "object") return DEFAULT_STATE;
  const loc = data.locations || {};
  return {
    ...DEFAULT_STATE,
    ...data,
    queue: Array.isArray(data.queue) ? data.queue : [],
    robot_pose: { ...DEFAULT_STATE.robot_pose, ...(data.robot_pose || {}) },
    locations: {
      dock: { ...DEFAULT_STATE.locations.dock, ...(loc.dock || {}) },
      racks: Array.isArray(loc.racks) ? loc.racks : [],
      drop_zones: Array.isArray(loc.drop_zones) ? loc.drop_zones : [],
      lanes: Array.isArray(loc.lanes) ? loc.lanes : [],
    },
  };
}

/** tone: idle | live | caution | fault | done */
export const STATE_META = {
  IDLE: { label: "Idle", tone: "idle", hint: "Ready for a mission" },
  RUNNING: { label: "Running", tone: "live", hint: "Working through the queue" },
  PAUSED: { label: "Paused", tone: "caution", hint: "Resume to continue the task" },
  NAVIGATING: { label: "Navigating", tone: "live", hint: "Driving to the next pose" },
  LIFTING: { label: "Lifting", tone: "live", hint: "Raising the rack" },
  DROPPING: { label: "Lowering", tone: "live", hint: "Setting the rack down" },
  RETURNING: { label: "Returning", tone: "live", hint: "Driving back to the dock" },
  ERROR: { label: "Emergency stop", tone: "fault", hint: "Check the event log, then release the E-stop" },
  COMPLETED: { label: "Completed", tone: "done", hint: "All tasks finished" },
};

export function stateMeta(state) {
  return STATE_META[state] || { label: state || "Unknown", tone: "idle", hint: "" };
}

const TRANSPORT_STEPS = [
  { id: "navigating_to_rack", label: (t) => `Drive to ${t.from_name || "rack"}` },
  { id: "centering", label: () => "Align under rack" },
  { id: "lifting", label: () => "Lift rack" },
  { id: "navigating_to_dest", label: (t) => `Carry to ${t.to_name || "destination"}` },
  { id: "inserting", label: () => "Insert into slot" },
  { id: "dropping", label: () => "Lower rack" },
];

export function taskSteps(task) {
  if (!task) return [];
  if (task.type === "transport") {
    return TRANSPORT_STEPS.map((s) => ({ id: s.id, label: s.label(task) }));
  }
  return [{ id: "navigating", label: `Drive to ${task.to_name || "goal"}` }];
}

export function taskTitle(task) {
  if (!task) return "";
  if (task.type === "transport") return `${task.from_name || "?"} → ${task.to_name || "?"}`;
  return task.to_name || "Goal";
}

export function taskKind(task) {
  return task?.type === "transport" ? "Move rack" : "Go to";
}

export function allSites(locations) {
  const l = locations || {};
  return [
    ...(l.racks || []).map((r) => ({ ...r, kind: "rack" })),
    ...(l.drop_zones || []).map((z) => ({ ...z, kind: "zone" })),
  ];
}

export function siteState(site) {
  return site.state || (site.kind === "zone" ? "free" : "occupied");
}
