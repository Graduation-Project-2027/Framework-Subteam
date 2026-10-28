/**
 * PreviewEngine — an in-browser stand-in for the robot computer.
 *
 * Replaces mission_manager_node (same JSON commands, state, feedback and
 * /plan), launcher_server.py (stack modes, logs, map library; same API as
 * stackClient.js) and the sensors RViz would show (lidar raycast against the
 * real floor plan, SLAM reveal while mapping, costmaps, rack legs, footprint,
 * a person crossing an aisle). Nothing here ever leaves the browser.
 */
import {
  loadBundledMap, makeGrid, cloneGrid, touch, inflate, planPath, raycast,
  gridToPGM, gridFromPGM, cellIndex,
} from "./grid";
import { DEFAULT_STATE } from "./mission";
import { PREVIEW_LOCATIONS } from "./previewFixture";
import { laneRoute, smoothPolyline, DEFAULT_RADIUS } from "./lanes";
import { detectDynamic } from "./live";
import { RACK_SIDE } from "./geometry";

const clone = (o) => JSON.parse(JSON.stringify(o));
const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const uid = () => Math.random().toString(16).slice(2, 10);
const now = () => Date.now() / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/;

const TICK_MS = 50;
const LIDAR_RANGE = 8;
const LIDAR_BEAMS = 360;

class ApiError extends Error {}

export class PreviewEngine {
  constructor(live) {
    this.live = live;
    const loc = clone(PREVIEW_LOCATIONS);
    this.state = {
      ...clone(DEFAULT_STATE),
      robot_pose: { x: loc.dock.x, y: loc.dock.y, yaw: loc.dock.yaw },
      locations: loc,
      queue: [],
      lane_corner_radius: DEFAULT_RADIUS,
    };
    this.log = [];
    this.plan = [];
    this.exec = null;
    this.teleop = null;
    this.resumeState = null;
    this.listeners = new Set();
    this.timer = null;
    this.tickCount = 0;
    this.truth = null;
    this.navMap = null;
    this.mapping = null;
    this.mappingDirty = 0;
    this.person = { x: -1.6, y: 2.75, dir: 1, min: -2.4, max: 0.0, r: 0.22, speed: 0.45, pause: 0 };

    this.library = new Map();
    this.trash = new Map();
    this.defaultMap = "Hang1";
    this.stack = { state: "starting", target: "sim", mode: "navigation", map: "Hang1", rviz: false, started: now(), lastExit: null };
    this.stackLogs = [];
    this.stackSeq = 0;

    this.ready = loadBundledMap().then((g) => {
      this.truth = g;
      this.library.set("Hang1", { grid: cloneGrid(g), orig: null, posegraph: true, modified: now() });
      this.bootStack({ target: "sim", mode: "navigation", map: "Hang1" }, 600);
    });

    this.say("Preview started. This robot is simulated in your browser.");
    this.handle({
      action: "add_task", type: "transport", rack_id: "racks_1778455475870",
      from_name: "R1", to_name: "R8",
      from: { ...loc.racks[0] }, to: { ...loc.racks[7] },
    });
  }

  /* ── wiring ─────────────────────────────────────────────────────── */
  get online() {
    return this.stack.state === "running";
  }

  subscribe(fn) {
    this.listeners.add(fn);
    fn(this.snapshot());
    if (!this.timer) this.timer = setInterval(() => this.tick(TICK_MS / 1000), TICK_MS);
    return () => {
      this.listeners.delete(fn);
      if (!this.listeners.size) { clearInterval(this.timer); this.timer = null; }
    };
  }

  snapshot() {
    return {
      online: this.online,
      starting: this.stack.state === "starting",
      missionState: { ...this.state, queue: this.state.queue.map((t) => ({ ...t })) },
      feedbackLog: this.log,
      plan: this.plan,
    };
  }

  emit() {
    const snap = this.snapshot();
    this.listeners.forEach((fn) => fn(snap));
  }

  say(message) {
    this.log = [...this.log.slice(-200), { timestamp: now(), message }];
  }

  stackLog(text, source = "launcher") {
    this.stackSeq += 1;
    this.stackLogs.push({ seq: this.stackSeq, t: now(), source, text });
    if (this.stackLogs.length > 600) this.stackLogs.shift();
  }

  send(cmd) {
    if (!this.online) return;
    try { this.handle(cmd); } catch (e) { this.say(`ERROR: ${e.message}`); }
    this.emit();
  }

  /* ── stack (launcher API) ───────────────────────────────────────── */
  async bootStack({ target, mode, map, rviz }, delay = 1400) {
    this.stack = { state: "starting", target, mode, map: mode === "navigation" ? map : null, rviz: !!rviz, started: now(), lastExit: null };
    const sim = target === "sim" ? " sim:=true" : "";
    this.stackLog(mode === "navigation"
      ? `ros2 launch amr_navigation navigation.launch.py${sim} rviz:=false map_file:=…/maps/${map}`
      : `ros2 launch amr_mapping mapping.launch.py${sim} rviz:=false`, "stack");
    this.stackLog(`ros2 launch amr_mission_control mission_control.launch.py${sim} start_gui:=false`, "stack");
    this.emit();
    await sleep(delay / 2);
    this.stackLog(mode === "navigation"
      ? "[slam_toolbox] Localization mode: deserialized pose graph."
      : "[slam_toolbox] Mapping mode: waiting for /scan_merged.", "stack");
    if (mode === "navigation") this.stackLog(`[map_server_static] Loaded ${map}.yaml, publishing /map_static.`, "stack");
    await sleep(delay / 2);
    if (mode === "navigation") this.stackLog("[lifecycle_manager_navigation] Managed nodes are active.", "stack");
    this.stackLog("[rosbridge_websocket] Rosbridge WebSocket server started on port 9090.", "stack");

    if (mode === "navigation") {
      this.navMap = cloneGrid(this.library.get(map).grid);
      this.mapping = null;
      this.live.map = this.navMap;
      this.live.mapStatic = this.navMap;
      this.live.globalCostmap = inflate(this.navMap);
    } else {
      this.navMap = null;
      const t = this.truth;
      this.mapping = makeGrid({ width: t.width, height: t.height, resolution: t.resolution, origin: t.origin });
      this.live.map = this.mapping;
      this.live.mapStatic = null;
      this.live.globalCostmap = null;
      this.live.localCostmap = null;
    }
    this.stack.state = "running";
    this.say(mode === "navigation"
      ? `Mission Manager started on map ${map}. Waiting for commands.`
      : "Mission Manager started. Mapping mode: drive the robot to build the map.");
    this.live.notify();
    this.emit();
  }

  haltStack(reason) {
    if (this.exec) this.halt("IDLE", "Mission stopped.");
    this.stack = { ...this.stack, state: "stopped", rviz: false, lastExit: reason || null };
    this.teleop = null;
    this.plan = [];
    this.live.reset();
    this.live.mapFile = null;
    this.emit();
  }

  stackStatus() {
    const running = this.stack.state !== "stopped";
    return {
      version: 2,
      preview: true,
      running,
      starting: this.stack.state === "starting",
      target: running ? this.stack.target : null,
      mode: running ? this.stack.mode : null,
      map: running ? this.stack.map : null,
      started_at: running ? this.stack.started : null,
      rviz: { running: running && this.stack.rviz, error: null },
      display: true,
      last_exit: null,
      default_map: this.library.has(this.defaultMap) ? this.defaultMap : [...this.library.keys()][0] || null,
      maps_dir: "this browser tab (Preview)",
      log_seq: this.stackSeq,
    };
  }

  mapInfo(name) {
    const m = this.library.get(name);
    const g = m.grid;
    return {
      name, width: g.width, height: g.height, resolution: g.resolution,
      origin: [g.origin.x, g.origin.y], negate: 0, occupied_thresh: 0.65, free_thresh: 0.196,
      size_m: [+(g.width * g.resolution).toFixed(2), +(g.height * g.resolution).toFixed(2)],
      has_image: true, has_posegraph: m.posegraph, edited: !!m.orig, modified: m.modified,
    };
  }

  requireMap(name) {
    if (!this.library.has(name)) throw new ApiError(`Map '${name}' not found.`);
    return this.library.get(name);
  }

  requireName(name) {
    if (!NAME_RE.test(name || "")) throw new ApiError("Use 1–40 letters, digits, '-' or '_' (start with a letter or digit).");
  }

  requireUnused(name, verb) {
    if (this.stack.state !== "stopped" && this.stack.map === name) {
      throw new ApiError(`'${name}' is in use by the running stack. Stop it before you ${verb} it.`);
    }
  }

  get api() {
    if (this._api) return this._api;
    const e = this;
    const ok = (extra = {}) => ({ ok: true, ...extra });
    this._api = {
      kind: "preview",
      status: async () => { await e.ready; return e.stackStatus(); },
      logs: async (after) => ({ lines: e.stackLogs.filter((l) => l.seq > (after || 0)), last: e.stackSeq }),
      launch: async ({ target, mode, map, rviz }) => {
        await e.ready;
        if (e.stack.state !== "stopped") throw new ApiError("The stack is already running. Stop it first.");
        if (mode === "navigation") {
          map = map || e.stackStatus().default_map;
          const m = e.requireMap(map);
          if (!m.posegraph) throw new ApiError(`Map '${map}' has no pose graph, so the robot can't localize on it.`);
        }
        e.stackLog(`Launching ${mode === "navigation" ? `navigation on ${map}` : "mapping"} (${target === "sim" ? "simulation" : "real robot"}).`);
        e.bootStack({ target, mode, map, rviz });
        if (rviz) e.stackLog("RViz would open on the robot computer; Preview has no RViz window.", "rviz");
        return ok(e.stackStatus());
      },
      stop: async () => {
        if (e.stack.state === "stopped") return ok({ message: "Nothing was running." });
        e.stackLog("Stopping the stack…");
        await sleep(500);
        e.haltStack();
        e.stackLog("Stack stopped.");
        return ok(e.stackStatus());
      },
      rviz: async (on) => {
        if (e.stack.state === "stopped") throw new ApiError("Start the stack first.");
        e.stack.rviz = !!on;
        e.stackLog(on ? "RViz would open on the robot computer; Preview has no RViz window." : "RViz closed.", "rviz");
        return ok(e.stackStatus());
      },
      maps: async () => {
        await e.ready;
        return {
          maps: [...e.library.keys()].sort((a, b) => a.localeCompare(b)).map((n) => e.mapInfo(n)),
          default: e.stackStatus().default_map,
          in_use: e.stack.state !== "stopped" ? e.stack.map : null,
          dir: "this browser tab (Preview)",
        };
      },
      pgm: async (name) => { await e.ready; return gridToPGM(e.requireMap(name).grid).buffer; },
      putPgm: async (name, bytes) => {
        const m = e.requireMap(name);
        const g = gridFromPGM(bytes.buffer || bytes, { resolution: m.grid.resolution, origin: [m.grid.origin.x, m.grid.origin.y] });
        if (g.width !== m.grid.width || g.height !== m.grid.height) throw new ApiError("Image size changed.");
        if (!m.orig) m.orig = m.grid;
        m.grid = g;
        m.modified = now();
        e.stackLog(`Map '${name}' image edited.`);
        if (e.stack.map === name && e.stack.state === "running") e.stackLog("Relaunch navigation to plan on the edited image.");
        return ok({ map: e.mapInfo(name) });
      },
      saveMap: async (name, overwrite) => {
        e.requireName(name);
        if (e.stack.state !== "running" || e.stack.mode !== "mapping") throw new ApiError("Start mapping first; only a running SLAM session can be saved.");
        if (e.library.has(name) && !overwrite) throw new ApiError(`A map called '${name}' already exists.`);
        e.stackLog(`Saving pose graph: ros2 service call /slam_toolbox/serialize_map … '${name}'`);
        await sleep(500);
        e.stackLog(`Saving image: ros2 run nav2_map_server map_saver_cli -f …/${name} --fmt pgm`);
        await sleep(500);
        e.library.set(name, { grid: cloneGrid(e.mapping), orig: null, posegraph: true, modified: now() });
        e.stackLog(`Map '${name}' saved.`);
        e.say(`Map saved as ${name}.`);
        e.emit();
        return ok({ map: e.mapInfo(name) });
      },
      rename: async (name, to) => {
        const m = e.requireMap(name);
        e.requireName(to);
        if (e.library.has(to)) throw new ApiError(`A map called '${to}' already exists.`);
        e.requireUnused(name, "rename");
        e.library.delete(name);
        e.library.set(to, m);
        if (e.defaultMap === name) e.defaultMap = to;
        e.stackLog(`Map '${name}' renamed to '${to}'.`);
        return ok({ map: e.mapInfo(to) });
      },
      duplicate: async (name, to) => {
        const m = e.requireMap(name);
        e.requireName(to);
        if (e.library.has(to)) throw new ApiError(`A map called '${to}' already exists.`);
        e.library.set(to, { grid: cloneGrid(m.grid), orig: m.orig ? cloneGrid(m.orig) : null, posegraph: m.posegraph, modified: now() });
        e.stackLog(`Map '${name}' duplicated as '${to}'.`);
        return ok({ map: e.mapInfo(to) });
      },
      setDefault: async (name) => { e.requireMap(name); e.defaultMap = name; e.stackLog(`Default map set to '${name}'.`); return ok(); },
      revert: async (name) => {
        const m = e.requireMap(name);
        if (!m.orig) throw new ApiError(`'${name}' has no edits to revert.`);
        m.grid = m.orig;
        m.orig = null;
        m.modified = now();
        e.stackLog(`Map '${name}' reverted to the saved scan.`);
        return ok({ map: e.mapInfo(name) });
      },
      remove: async (name) => {
        const m = e.requireMap(name);
        e.requireUnused(name, "delete");
        const id = `${name}__${Math.round(now())}`;
        e.trash.set(id, { name, m });
        e.library.delete(name);
        e.stackLog(`Map '${name}' moved to trash.`);
        return ok({ trash_id: id });
      },
      restore: async (id) => {
        const t = e.trash.get(id);
        if (!t) throw new ApiError("Nothing to restore.");
        if (e.library.has(t.name)) throw new ApiError(`A map called '${t.name}' exists again; rename it first.`);
        e.library.set(t.name, t.m);
        e.trash.delete(id);
        e.stackLog(`Map '${t.name}' restored.`);
        return ok({ map: e.mapInfo(t.name) });
      },
    };
    return this._api;
  }

  /* ── command handling (mirrors mission_manager_node) ────────────── */
  handle(cmd) {
    const s = this.state;
    const mapping = this.stack.mode === "mapping";
    const needsNav = ["start", "return_to_dock", "navigate_to"];
    if (mapping && needsNav.includes(cmd.action)) {
      return this.say("Mapping mode: there is no Nav2 running. Save the map and launch navigation to send the robot.");
    }
    switch (cmd.action) {
      case "add_task": return this.addTask(cmd);
      case "remove_task":
        s.queue = s.queue.filter((t) => t.id !== cmd.id);
        return this.say(`Task removed: ${cmd.id}`);
      case "reorder_tasks": {
        const map = Object.fromEntries(s.queue.map((t) => [t.id, t]));
        s.queue = (cmd.order || []).map((id) => map[id]).filter(Boolean);
        return this.say("Task queue reordered.");
      }
      case "start": return this.start();
      case "pause":
        if (["RUNNING", "NAVIGATING", "LIFTING", "DROPPING", "RETURNING"].includes(s.state)) {
          this.resumeState = s.state;
          s.state = "PAUSED";
          this.say("Mission paused.");
        }
        return;
      case "resume":
        if (s.state === "PAUSED") {
          s.state = this.resumeState || "RUNNING";
          this.say("Mission resumed.");
        }
        return;
      case "stop": return this.halt("IDLE", "Mission stopped.");
      case "estop": return this.halt("ERROR", "EMERGENCY STOP ACTIVATED");
      case "estop_release":
        if (s.state === "ERROR") s.state = "IDLE";
        return this.say("Emergency stop release requested.");
      case "return_to_dock": {
        const d = s.locations.dock;
        s.queue.unshift({
          id: "return_dock", type: "navigate", from_name: "Current",
          to_name: d.name || "Dock", to: { x: d.x, y: d.y, yaw: d.yaw || 0 }, status: "queued",
        });
        this.say("Return to dock queued.");
        if (s.state === "IDLE") this.start();
        return;
      }
      case "navigate_to":
        s.queue.unshift({
          id: uid(), type: "navigate", from_name: "Current",
          to_name: cmd.name || `(${cmd.x.toFixed(1)}, ${cmd.y.toFixed(1)})`,
          to: { x: cmd.x, y: cmd.y, yaw: cmd.yaw || 0 }, status: "queued",
        });
        if (s.state === "IDLE") this.start();
        return;
      case "update_locations": {
        const prev = {};
        [...s.locations.racks, ...s.locations.drop_zones].forEach((l) => { prev[l.id] = l.state; });
        const keep = (arr, def) => (arr || []).map((it) => ({ ...it, state: it.state || prev[it.id] || def }));
        s.locations = { ...s.locations, racks: keep(cmd.racks, "occupied"), drop_zones: keep(cmd.drop_zones, "free") };
        return this.say(`Locations updated: ${s.locations.racks.length} racks, ${s.locations.drop_zones.length} drop zones.`);
      }
      case "update_dock":
        s.locations = { ...s.locations, dock: { x: cmd.x, y: cmd.y, yaw: cmd.yaw || 0, name: cmd.name || "Dock" } };
        return this.say(`Dock updated: ${s.locations.dock.name}.`);
      case "update_lanes":
        s.locations = { ...s.locations, lanes: cmd.lanes || [] };
        return this.say(`Lanes updated: ${s.locations.lanes.length} polyline(s).`);
      case "set_location_state": return this.setSiteState(cmd.id, cmd.state);
      case "set_speed":
        s.speed = Math.max(0.1, Math.min(1, Number(cmd.speed) || 0.4));
        return this.say(`Max speed set to ${s.speed.toFixed(2)} m/s`);
      case "teleop":
        // Differential drive: sideways (ly) is ignored, as on the robot.
        this.teleop = { lx: cmd.lx || 0, az: cmd.az || 0, until: performance.now() + 350 };
        return;
      case "lift":
        return this.say(`Lift commanded to ${Number(cmd.height || 0).toFixed(2)}m`);
      default:
        return this.say(`Unknown command: ${cmd.action}`);
    }
  }

  siteById(id) {
    const l = this.state.locations;
    return [...l.racks, ...l.drop_zones].find((x) => x.id === id);
  }

  setSiteState(id, state, quiet = false) {
    const l = this.state.locations;
    let name = null;
    const upd = (arr) => arr.map((x) => {
      if (x.id !== id || x.state === state) return x;
      name = x.name || id;
      return { ...x, state };
    });
    const racks = upd(l.racks);
    const drop_zones = upd(l.drop_zones);
    if (!name) return;
    this.state.locations = { ...l, racks, drop_zones };
    if (!quiet) this.say(`'${name}' is now ${state.toUpperCase()}.`);
  }

  addTask(cmd) {
    if ((cmd.type || "transport") === "transport") {
      const src = this.siteById(cmd.from?.id);
      const dst = this.siteById(cmd.to?.id);
      if (src && src.state === "free") {
        return this.say(`ERROR: Cannot add task — source '${cmd.from_name}' is FREE (no rack to pick up).`);
      }
      if (dst && dst.state === "occupied") {
        return this.say(`ERROR: Cannot add task — destination '${cmd.to_name}' is OCCUPIED (would crash into an existing rack).`);
      }
    }
    this.state.queue.push({
      id: cmd.id || uid(), type: cmd.type || "transport", rack_id: cmd.rack_id || "",
      from_name: cmd.from_name || "", to_name: cmd.to_name || "",
      from: cmd.from || {}, to: cmd.to || {}, status: "queued",
    });
    this.say(`Task added: ${cmd.from_name || "Current"} → ${cmd.to_name}`);
  }

  start() {
    const s = this.state;
    if (s.state !== "IDLE") return this.say("Cannot start: not in IDLE state.");
    if (!s.queue.length) return this.say("Queue is empty — add a task first.");
    s.state = "RUNNING";
    this.say(`Mission started with ${s.queue.length} task(s).`);
    this.nextTask();
  }

  halt(state, message) {
    const s = this.state;
    s.state = state;
    if (this.exec?.task) this.exec.task.status = "queued";
    this.exec = null;
    s.current_task = null;
    s.current_step = "";
    s.progress = 0;
    this.plan = [];
    this.say(message);
  }

  /* ── execution ──────────────────────────────────────────────────── */
  nextTask() {
    const s = this.state;
    const task = s.queue[0];
    if (!task) {
      s.current_task = null;
      const d = s.locations.dock;
      this.say("All tasks complete. Returning to dock...");
      s.state = "RETURNING";
      this.exec = { task: null, steps: [{ kind: "drive", step: "", goal: d, state: "RETURNING" }], idx: -1 };
      return this.advance();
    }
    task.status = "running";
    s.current_task = task;
    const steps = task.type === "transport"
      ? [
          { kind: "drive", step: "navigating_to_rack", goal: task.from, state: "NAVIGATING" },
          { kind: "wait", step: "centering", dur: 1.4, state: "NAVIGATING" },
          { kind: "wait", step: "lifting", dur: 2.2, state: "LIFTING", done: () => {
            s.carrying = true;
            this.setSiteState(task.from?.id, "free");
          } },
          { kind: "drive", step: "navigating_to_dest", goal: task.to, state: "NAVIGATING" },
          { kind: "wait", step: "inserting", dur: 1.4, state: "NAVIGATING" },
          { kind: "wait", step: "dropping", dur: 2.2, state: "DROPPING", done: () => {
            s.carrying = false;
            this.setSiteState(task.to?.id, "occupied");
          } },
        ]
      : [{ kind: "drive", step: "navigating", goal: task.to, state: "NAVIGATING" }];
    this.say(`Executing: ${task.from_name ? `${task.from_name} → ` : ""}${task.to_name}`);
    this.exec = { task, steps, idx: -1 };
    this.advance();
  }

  routeTo(goal) {
    const pose = this.state.robot_pose;
    const lanes = this.state.locations.lanes || [];
    if (lanes.length) {
      const wps = laneRoute(lanes, pose, goal);
      if (wps && wps.length >= 2) {
        const curve = smoothPolyline(wps, this.state.lane_corner_radius, 0.1);
        const toLane = planPath(this.navMap, pose, wps[0]);
        const offLane = planPath(this.navMap, wps[wps.length - 1], goal);
        this.say(`  Lane routing: ${wps.length} waypoint(s), following the rounded lane curve.`);
        return [...toLane, ...curve.slice(1), ...offLane.slice(1)];
      }
    }
    return planPath(this.navMap, pose, goal);
  }

  advance() {
    const ex = this.exec;
    const s = this.state;
    ex.idx += 1;
    if (ex.idx >= ex.steps.length) return this.finishTask();
    const st = ex.steps[ex.idx];
    s.current_step = st.step;
    s.state = st.state;
    s.progress = 0;
    ex.t = 0;
    if (st.kind === "drive") {
      ex.path = this.routeTo(st.goal);
      ex.wp = 1;
      ex.total = pathLength(ex.path);
      this.plan = ex.path;
      if (st.step === "navigating_to_rack") this.say(`Navigating to ${ex.task.from_name}...`);
      if (st.step === "navigating_to_dest") this.say(`Carrying rack to ${ex.task.to_name}...`);
    } else {
      this.plan = [];
      if (st.step === "centering") this.say("Centering under rack...");
      if (st.step === "lifting") this.say("Lifting rack...");
      if (st.step === "dropping") this.say("Lowering rack...");
    }
  }

  finishTask() {
    const s = this.state;
    const ex = this.exec;
    this.plan = [];
    s.progress = 1;
    if (!ex.task) {
      this.exec = null;
      s.state = "IDLE";
      s.current_step = "";
      s.progress = 0;
      return this.say("Docked. Mission complete.");
    }
    ex.task.status = "done";
    s.queue = s.queue.filter((t) => t.id !== ex.task.id);
    this.say(`Task complete: ${ex.task.to_name}`);
    this.nextTask();
  }

  /* ── simulation ─────────────────────────────────────────────────── */
  tick(dt) {
    this.tickCount += 1;
    if (!this.online || !this.truth) {
      if (this.tickCount % 4 === 0) this.emit();
      return;
    }
    const s = this.state;
    const pose = s.robot_pose;
    const ex = this.exec;
    this.movePerson(dt);
    const moving = ex && s.state !== "PAUSED" && ex.steps[ex.idx]?.kind === "drive";

    if (this.teleop && !moving) {
      const t = this.teleop;
      if (performance.now() > t.until) this.teleop = null;
      else {
        const k = s.speed / 0.4;
        pose.x += t.lx * Math.cos(pose.yaw) * k * dt;
        pose.y += t.lx * Math.sin(pose.yaw) * k * dt;
        pose.yaw = wrap(pose.yaw + t.az * k * dt);
      }
    }

    if (ex && s.state !== "PAUSED" && s.state !== "IDLE" && s.state !== "ERROR") {
      const st = ex.steps[ex.idx];
      if (st.kind === "wait") {
        ex.t += dt;
        s.progress = Math.min(1, ex.t / st.dur);
        if (ex.t >= st.dur) { st.done?.(); this.advance(); }
      } else if (!this.personAhead()) {
        this.drive(ex, st, dt);
      }
    }

    s.robot_pose = { ...pose };
    if (this.tickCount % 2 === 0) {
      this.sense();
      this.emit();
    }
    if (this.tickCount % 8 === 0) this.senseSlow();
  }

  movePerson(dt) {
    const p = this.person;
    if (p.pause > 0) { p.pause -= dt; return; }
    p.x += p.dir * p.speed * dt;
    if (p.x > p.max || p.x < p.min) {
      p.x = Math.max(p.min, Math.min(p.max, p.x));
      p.dir *= -1;
      p.pause = 2;
    }
  }

  personAhead() {
    const r = this.state.robot_pose, p = this.person;
    const dx = p.x - r.x, dy = p.y - r.y;
    const d = Math.hypot(dx, dy);
    if (d > 1.0) return false;
    const ahead = (dx * Math.cos(r.yaw) + dy * Math.sin(r.yaw)) / d;
    if (ahead > 0.3) {
      if (!this._waitingSaid) { this.say("Obstacle ahead: waiting for the path to clear."); this._waitingSaid = true; }
      return true;
    }
    this._waitingSaid = false;
    return false;
  }

  beam(x, y, ang) {
    let d = raycast(this.truth, x, y, ang, LIDAR_RANGE);
    const p = this.person;
    const ox = x - p.x, oy = y - p.y;
    const b = ox * Math.cos(ang) + oy * Math.sin(ang);
    const c = ox * ox + oy * oy - p.r * p.r;
    const disc = b * b - c;
    if (disc >= 0) {
      const t = -b - Math.sqrt(disc);
      if (t > 0 && t < d) d = t;
    }
    return d;
  }

  sense() {
    const r = this.state.robot_pose;
    const merged = new Float32Array(LIDAR_BEAMS * 2);
    const front = [], rear = [];
    let n = 0;
    const reveal = this.mapping;
    for (let i = 0; i < LIDAR_BEAMS; i++) {
      const rel = -Math.PI + (i * 2 * Math.PI) / LIDAR_BEAMS;
      const ang = r.yaw + rel;
      const d = this.beam(r.x, r.y, ang);
      if (reveal) this.revealRay(r.x, r.y, ang, d);
      if (!Number.isFinite(d)) continue;
      const x = r.x + d * Math.cos(ang), y = r.y + d * Math.sin(ang);
      merged[n++] = x;
      merged[n++] = y;
      if (Math.abs(rel) < 2.18) front.push(x, y);
      if (Math.abs(rel) > 0.96) rear.push(x, y);
    }
    const pts = merged.subarray(0, n);
    const t = performance.now();
    this.live.scans = {
      merged: { points: pts, t },
      front: { points: Float32Array.from(front), t },
      rear: { points: Float32Array.from(rear), t },
    };
    this.live.dynamic = detectDynamic(pts, this.navMap || this.mapping);
    if (reveal && (this.mappingDirty += 1) % 10 === 0) touch(this.mapping);
    this.live.notify();
  }

  revealRay(x, y, ang, d) {
    const g = this.mapping;
    const end = Math.min(Number.isFinite(d) ? d : LIDAR_RANGE, LIDAR_RANGE);
    const step = g.resolution;
    const cx = Math.cos(ang), cy = Math.sin(ang);
    for (let t = 0; t < end - step; t += step) {
      const i = cellIndex(g, x + cx * t, y + cy * t);
      if (i >= 0 && g.data[i] !== 100) g.data[i] = 0;
    }
    if (Number.isFinite(d) && d < LIDAR_RANGE) {
      const i = cellIndex(g, x + cx * (d + step * 0.5), y + cy * (d + step * 0.5));
      if (i >= 0) g.data[i] = 100;
    }
  }

  senseSlow() {
    const r = this.state.robot_pose;
    const s = this.state;
    const live = this.live;
    // Footprint (robot or carried rack), map frame.
    const hl = s.carrying ? RACK_SIDE / 2 + 0.05 : 0.38, hw = s.carrying ? RACK_SIDE / 2 + 0.05 : 0.35;
    live.footprint = [[hl, hw], [hl, -hw], [-hl, -hw], [-hl, hw]].map(([x, y]) => ({
      x: r.x + x * Math.cos(r.yaw) - y * Math.sin(r.yaw),
      y: r.y + x * Math.sin(r.yaw) + y * Math.cos(r.yaw),
    }));
    // Legs of occupied racks, like /rack_obstacles.
    const legs = [];
    const h = RACK_SIDE / 2 - 0.03;
    for (const rk of [...s.locations.racks, ...s.locations.drop_zones]) {
      if ((rk.state || "occupied") !== "occupied") continue;
      for (const [lx, ly] of [[h, h], [h, -h], [-h, -h], [-h, h]]) {
        legs.push(rk.x + lx * Math.cos(rk.yaw || 0) - ly * Math.sin(rk.yaw || 0), rk.y + lx * Math.sin(rk.yaw || 0) + ly * Math.cos(rk.yaw || 0));
      }
    }
    live.rackCloud = Float32Array.from(legs);
    // Rolling 3 x 3 m local costmap from the scan, in the map frame.
    if (this.stack.mode === "navigation") {
      const res = 0.05, size = 60;
      const ox = Math.round((r.x - 1.5) / res) * res, oy = Math.round((r.y - 1.5) / res) * res;
      const local = makeGrid({ width: size, height: size, resolution: res, origin: { x: ox, y: oy }, data: new Int8Array(size * size) });
      const pts = live.scans.merged?.points || [];
      for (let i = 0; i < pts.length; i += 2) {
        const k = cellIndex(local, pts[i], pts[i + 1]);
        if (k >= 0) local.data[k] = 100;
      }
      for (let i = 0; i < legs.length; i += 2) {
        const k = cellIndex(local, legs[i], legs[i + 1]);
        if (k >= 0) local.data[k] = 100;
      }
      live.localCostmap = inflate(local, { unknown: false });
    }
    live.notify();
  }

  drive(ex, st, dt) {
    const s = this.state;
    const pose = s.robot_pose;
    const v = s.speed;
    const turnRate = 2.2;
    const path = ex.path;

    if (ex.wp >= path.length) {
      const goalYaw = st.goal.yaw || 0;
      const err = wrap(goalYaw - pose.yaw);
      if (Math.abs(err) < 0.03) { pose.yaw = goalYaw; s.progress = 1; return this.advance(); }
      pose.yaw = wrap(pose.yaw + Math.sign(err) * Math.min(Math.abs(err), turnRate * dt));
      return;
    }

    const target = path[ex.wp];
    const dx = target.x - pose.x, dy = target.y - pose.y;
    const dist = Math.hypot(dx, dy);
    if (dist < 0.06) { ex.wp += 1; return; }
    const heading = Math.atan2(dy, dx);
    const err = wrap(heading - pose.yaw);
    pose.yaw = wrap(pose.yaw + Math.sign(err) * Math.min(Math.abs(err), turnRate * dt));
    if (Math.abs(err) < 0.7) {
      const step = Math.min(dist, v * dt * Math.max(0.25, Math.cos(err)));
      pose.x += (dx / dist) * step;
      pose.y += (dy / dist) * step;
    }
    const remaining = dist + pathLength(path.slice(ex.wp));
    s.progress = ex.total > 0 ? Math.max(0, Math.min(1, 1 - remaining / ex.total)) : 1;
    this.plan = [{ x: pose.x, y: pose.y }, ...path.slice(ex.wp)];
  }
}

function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return d;
}
