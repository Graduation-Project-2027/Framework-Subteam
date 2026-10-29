/**
 * FleetPreview — an in-browser stand-in for fleet_server.py and the robots
 * it manages. Same API as fleetClient.js, same traffic rules (traffic.js),
 * same lane routing and planner as the single-robot Preview. Three robots
 * work the real Hang1 layout; a fourth turns up on the network after a few
 * seconds, waiting for first-time setup. Nothing leaves the browser.
 */
import { loadBundledMap, cloneGrid, touch, planPath, gridToPGM, gridFromPGM, cellIndex } from "./grid";
import { lanePath, nearestLanePoint, DEFAULT_RADIUS } from "./lanes";
import { PREVIEW_LOCATIONS } from "./previewFixture";
import { DEFAULT_STATE } from "./mission";
import { trafficTick, createTrafficMemory, approachFoot, radiusOf, isDriving } from "./traffic";

const clone = (o) => JSON.parse(JSON.stringify(o));
const now = () => Date.now() / 1000;
const wrap = (a) => Math.atan2(Math.sin(a), Math.cos(a));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ID_RE = /^[A-Za-z0-9][A-Za-z0-9-]{0,15}$/;
const MAP_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$/;
const TICK_MS = 50;

class ApiError extends Error {}

/* The fleet's shared layout: the team's racks plus drop zones in the empty
   slots, and a lane loop round the rack rows. */
const LAYOUT = {
  racks: PREVIEW_LOCATIONS.racks,
  drop_zones: [
    { id: "zone_z1", name: "Z1", x: 2.94, y: 4.95, yaw: -1.55, state: "free" },
    { id: "zone_z2", name: "Z2", x: 4.1, y: 4.95, yaw: -1.55, state: "free" },
    { id: "zone_z3", name: "Z3", x: 5.28, y: 3.64, yaw: 1.6, state: "free" },
  ],
  lanes: [
    { id: "lane_loop", name: "Main loop", oneway: true, points: [
      { x: -1.2, y: 2.3 }, { x: 6.8, y: 2.3 }, { x: 6.8, y: 6.6 }, { x: -1.2, y: 6.6 }, { x: -1.2, y: 2.3 },
    ] },
  ],
};

const MAPS = {
  Hang1: { fingerprint: "1747fe5692a4", has_posegraph: true },
  "Hang1-annex": { fingerprint: "9c03e1aa71b2", has_posegraph: true },
  Lab2: { fingerprint: "44d0e7c1f9a8", has_posegraph: true },
};

const ROBOTS = [
  { machine: "b827eb3fa101", host: "10.42.0.21", hostname: "amr-pi-01", id: "AMR-01", name: "Atlas", domain_id: 11, priority: 1,
    dock: { x: -2.6, y: 3.0, yaw: Math.PI }, maps: { Hang1: "1747fe5692a4", "Hang1-annex": "9c03e1aa71b2", Lab2: "44d0e7c1f9a8" } },
  { machine: "b827eb3fa102", host: "10.42.0.22", hostname: "amr-pi-02", id: "AMR-02", name: "Bolt", domain_id: 12, priority: 0,
    dock: { x: -2.6, y: 3.9, yaw: Math.PI }, maps: { Hang1: "1747fe5692a4", Lab2: "44d0e7c1f9a8" } },
  { machine: "b827eb3fa103", host: "10.42.0.23", hostname: "amr-pi-03", id: "AMR-03", name: "", domain_id: 13, priority: 0,
    dock: { x: -2.6, y: 4.8, yaw: Math.PI }, maps: { Hang1: "1747fe5692a4", Lab2: "2b7d90c4e113" } },
];
const NEWCOMER = { machine: "d83add41c9e4", host: "10.42.0.24", hostname: "amr-pi-04", id: null, name: "", domain_id: null, priority: 0,
  dock: { x: -2.6, y: 5.7, yaw: Math.PI }, maps: {} };

function pathLength(pts) {
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return d;
}

class SimRobot {
  constructor(spec, layout) {
    this.machine = spec.machine;
    this.host = spec.host;
    this.info = {
      machine: spec.machine, id: spec.id, name: spec.name, domain_id: spec.domain_id, max_speed: 0.5,
      priority: spec.priority, notes: "", host: spec.hostname, launcher: 8088, rosbridge: 9090,
      configured: !!spec.id, default_map: "Hang1",
      stack: { running: true, mode: "navigation", map: "Hang1", target: "real" },
    };
    this.maps = Object.entries(spec.maps).map(([name, fingerprint]) => ({ name, fingerprint, has_posegraph: true }));
    this.dock = { ...spec.dock, name: `${spec.id || "New"} dock` };
    this.adopted = !!spec.id;
    this.seen = now();
    this.state = {
      state: "IDLE", robot_pose: { ...spec.dock }, current_task: null, current_step: "", progress: 0,
      carrying: false, speed: 0.5, queue: [], locations: { dock: this.dock, ...clone(layout) },
    };
    this.plan = [];
    this.exec = null;
    this.log = [];
    this.hold = null;
    this.clearSince = null;
    this.movingSince = null;
    this.claim = [];
    this.replannedAt = 0;
  }

  get label() { return this.info.id || `New ${this.machine.slice(0, 4)}`; }
  get linked() { return this.adopted && this.info.stack.running; }

  say(message) {
    this.log = [...this.log.slice(-60), { timestamp: now(), message }];
  }

  public() {
    const s = this.state;
    return {
      key: this.machine, id: this.info.id, label: this.label, configured: !!this.info.id, name: this.info.name,
      host: this.host, hostname: this.info.host, launcher: 8088, rosbridge: 9090,
      settings: { id: this.info.id, name: this.info.name, domain_id: this.info.domain_id, max_speed: this.info.max_speed,
        priority: this.info.priority, notes: this.info.notes },
      stack: this.info.stack,
      status: "online",
      adopted: this.adopted,
      linked: this.linked,
      link_error: null,
      mission: this.adopted ? {
        state: s.state, pose: { ...s.robot_pose }, step: s.current_step, task: s.current_task, progress: s.progress,
        carrying: s.carrying, speed: s.speed, queue: s.queue.length,
        fleet: { managed: true, held: !!this.hold, by: this.hold?.by_label || "", reason: this.hold?.reason || "" },
      } : null,
      plan: this.plan.filter((_, i) => i % 2 === 0),
      claim: this.claim,
      hold: this.hold,
      layout_sync: this.adopted ? "synced" : "off",
      log: this.log.slice(-12),
    };
  }
}

export class FleetPreview {
  constructor() {
    this.kind = "preview";
    this.url = "preview";
    this.layout = { rev: 1, map: "Hang1", sync: true, source: "AMR-01", updated: now(), lane_corner_radius: DEFAULT_RADIUS, ...clone(LAYOUT) };
    this.library = Object.entries(MAPS).map(([name, m]) => ({ name, ...m, modified: now() - 86400 * 3, bytes: 612000 }));
    this.robots = ROBOTS.map((r) => new SimRobot(r, this.siteLayout()));
    this.pending = new SimRobot(NEWCOMER, this.siteLayout());
    this.pending.adopted = false;
    // A robot fresh off the bench: no maps yet, stack not started.
    this.pending.info.stack = { running: false, mode: null, map: null, target: null };
    this.events = [];
    this.conflicts = [];
    this.trafficMem = createTrafficMemory();
    this.autopilot = true;
    this.lastAuto = 0;
    this.lastTraffic = 0;
    this.started = now();
    this.truth = null;
    this.grid = null;
    this.event("Fleet manager running in Preview: three simulated robots on Hang1.");
    this.ready = loadBundledMap().then((g) => {
      this.truth = g;
      this.grid = g;
    });
    this.robots.forEach((r) => r.say("Fleet manager connected; drives now follow fleet traffic control."));
    this.timer = null;
    if (import.meta.env.DEV) window.__fleetPreview = this; // for staging scenarios from the console
  }

  start() { if (!this.timer) this.timer = setInterval(() => this.tick(TICK_MS / 1000), TICK_MS); }
  stop() { clearInterval(this.timer); this.timer = null; }

  siteLayout() {
    const l = this.layout || LAYOUT;
    return { racks: clone(l.racks), drop_zones: clone(l.drop_zones), lanes: clone(l.lanes) };
  }

  event(text, robot = null, tone = "info") {
    this.events = [...this.events.slice(-120), { t: now(), robot, text, tone }];
  }

  all() { return this.pending && now() - this.started > 6 ? [...this.robots, this.pending] : this.robots; }
  fleet() { return this.robots.filter((r) => r.adopted); }
  get(key) {
    const r = this.all().find((x) => x.machine === key);
    if (!r) throw new ApiError("No such robot.");
    return r;
  }

  /* ── API (fleetClient.js shapes) ──────────────────────────────────── */
  async state() {
    const robots = this.all().map((r) => r.public())
      .sort((a, b) => (a.adopted === b.adopted ? a.label.localeCompare(b.label) : a.adopted ? -1 : 1));
    return {
      manager: "preview", t: now(), robots,
      traffic: { conflicts: this.conflicts, updated: this.lastTraffic },
      warnings: this.warnings(),
      events: this.events.slice(-40),
      layout: { rev: this.layout.rev, map: this.layout.map, sync: this.layout.sync, source: this.layout.source,
        updated: this.layout.updated, lane_corner_radius: this.layout.lane_corner_radius },
      preview: { autopilot: this.autopilot },
    };
  }

  async getLayout() { return clone(this.layout); }

  async putLayout(body) {
    await sleep(120);
    for (const k of ["lanes", "racks", "drop_zones"]) if (body[k]) this.layout[k] = clone(body[k]);
    if ("sync" in body) this.layout.sync = !!body.sync;
    if ("lane_corner_radius" in body) this.layout.lane_corner_radius = Math.max(0, Math.min(2, +body.lane_corner_radius));
    this.layout.rev += 1;
    this.layout.source = "fleet";
    this.layout.updated = now();
    if (this.layout.sync) this.fleet().forEach((r) => { r.state.locations = { dock: r.dock, ...this.siteLayout() }; r.say(`Layout rev ${this.layout.rev} received from the fleet.`); });
    this.event(`Layout rev ${this.layout.rev}: ${this.layout.lanes.length} lanes, ${this.layout.racks.length} racks, ${this.layout.drop_zones.length} zones.`);
    return { ok: true, layout: clone(this.layout) };
  }

  async importLayout(key) {
    const r = this.get(key);
    return this.putLayout({ ...r.state.locations, source: r.label });
  }

  async maps() {
    await this.ready;
    const robots = {};
    this.all().forEach((r) => { robots[r.machine] = r.maps; });
    const meta = this.truth ? { resolution: this.truth.resolution, origin: [this.truth.origin.x, this.truth.origin.y], negate: 0, occupied_thresh: 0.65, free_thresh: 0.196 } : {};
    return { library: this.library.map((m) => ({ ...m, ...meta })), robots, dir: "~/.ros/amr_fleet/maps" };
  }

  async pgm() {
    await this.ready;
    return gridToPGM(this.truth).buffer;
  }

  async pull(key, name, { as, overwrite } = {}) {
    await sleep(500);
    const r = this.get(key);
    const src = r.maps.find((m) => m.name === name);
    if (!src) throw new ApiError(`Map '${name}' not found.`);
    const target = as || name;
    if (!MAP_RE.test(target)) throw new ApiError("Use 1–40 letters, digits, '-' or '_' (start with a letter or digit).");
    const have = this.library.find((m) => m.name === target);
    if (have && !overwrite) throw new ApiError(`A map named '${target}' already exists.`);
    this.library = [...this.library.filter((m) => m.name !== target), { name: target, fingerprint: src.fingerprint, has_posegraph: true, modified: now(), bytes: 612000 }]
      .sort((a, b) => a.name.localeCompare(b.name));
    this.event(`Map ${target} (${src.fingerprint}) copied from ${r.label} to the fleet library.`, key);
    return { ok: true, fingerprint: src.fingerprint };
  }

  async push(name, keys, overwrite) {
    const lib = this.library.find((m) => m.name === name);
    if (!lib) throw new ApiError(`Map '${name}' is not in the fleet library.`);
    const results = {};
    for (const key of keys) {
      await sleep(350);
      const r = this.all().find((x) => x.machine === key);
      if (!r) { results[key] = { ok: false, error: "Robot is offline." }; continue; }
      if (r.info.stack.running && r.info.stack.map === name) {
        results[key] = { ok: false, error: `'${name}' is in use by the running stack. Stop it before you replace it.` };
        continue;
      }
      if (r.maps.some((m) => m.name === name) && !overwrite) {
        results[key] = { ok: false, error: `A map named '${name}' already exists.` };
        continue;
      }
      r.maps = [...r.maps.filter((m) => m.name !== name), { name, fingerprint: lib.fingerprint, has_posegraph: true }]
        .sort((a, b) => a.name.localeCompare(b.name));
      r.say(`Map '${name}' received from the fleet (${lib.fingerprint}).`);
      this.event(`Map ${name} sent to ${r.label}.`, key);
      results[key] = { ok: true };
    }
    return { ok: true, results };
  }

  /** Edited floor plan: new library copy, sent to every robot with the same
   *  copy, reloaded live where the robot navigates on it. */
  async putMapPgm(name, bytes) {
    await this.ready;
    await sleep(300);
    const lib = this.library.find((m) => m.name === name);
    if (!lib) throw new ApiError(`Map '${name}' is not in the fleet library.`);
    const g = gridFromPGM(bytes.buffer ? bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) : bytes,
      { resolution: this.truth.resolution, origin: this.truth.origin, negate: 0, occupied_thresh: 0.65, free_thresh: 0.196 });
    this.truth = g;
    this.grid = g;
    const oldFp = lib.fingerprint;
    lib.fingerprint = Math.random().toString(16).slice(2, 14).padEnd(12, "0");
    lib.modified = now();
    const results = {};
    let live = 0;
    for (const r of this.all()) {
      const m = r.maps.find((x) => x.name === name);
      if (!m) continue;
      if (m.fingerprint !== oldFp) { results[r.machine] = { ok: false, error: "Has a different copy; send it the full map first." }; continue; }
      m.fingerprint = lib.fingerprint;
      const running = r.info.stack.running && r.info.stack.map === name;
      if (running) live += 1;
      r.say(running ? `Map '${name}' edited and reloaded live.` : `Map '${name}' image edited.`);
      results[r.machine] = { ok: true, live: running };
    }
    const sent = Object.values(results).filter((x) => x.ok).length;
    this.event(`Map ${name} edited (${lib.fingerprint}); sent to ${sent} robot(s), applied live on ${live}.`);
    return { ok: true, fingerprint: lib.fingerprint, results };
  }

  async removeMap(name) {
    this.library = this.library.filter((m) => m.name !== name);
    this.event(`Map ${name} removed from the fleet library.`);
    return { ok: true };
  }

  async add(host) {
    await sleep(600);
    const h = host.trim().split(":")[0];
    const r = this.all().find((x) => x.host === h || x.info.host === h);
    if (!r) throw new ApiError("The robot's launcher isn't reachable.");
    return { ok: true, robot: r.public() };
  }

  async adopt(key) {
    const r = this.get(key);
    if (!r.info.id) throw new ApiError("Give the robot an ID first (first-time setup).");
    if (this.fleet().some((o) => o !== r && o.info.id.toLowerCase() === r.info.id.toLowerCase())) {
      throw new ApiError(`${r.info.id} is already in the fleet. Change this robot's ID first.`);
    }
    r.adopted = true;
    if (r === this.pending) { this.robots.push(r); this.pending = null; }
    r.state.locations = { dock: r.dock, ...this.siteLayout() };
    r.say("Fleet manager connected; drives now follow fleet traffic control.");
    this.event(`${r.label} added to the fleet.`, key);
    return { ok: true };
  }

  async release(key) {
    const r = this.get(key);
    r.adopted = false;
    r.hold = null;
    r.exec = null;
    r.plan = [];
    Object.assign(r.state, { state: "IDLE", current_task: null, current_step: "", queue: [] });
    this.event(`${r.label} removed from the fleet.`, key);
    return { ok: true };
  }

  async forget(key) {
    await this.release(key);
    this.robots = this.robots.filter((r) => r.machine !== key);
    return { ok: true };
  }

  async settings(key, body) {
    await sleep(200);
    const r = this.get(key);
    const upd = {};
    if ("id" in body) {
      const id = String(body.id || "").trim();
      if (!ID_RE.test(id)) throw new ApiError("Robot ID: 1–16 letters, digits or '-' (e.g. AMR-03).");
      const clash = this.all().find((o) => o !== r && (o.info.id || "").toLowerCase() === id.toLowerCase());
      if (clash) throw new ApiError(`${clash.label} already uses that ID.`);
      upd.id = id;
    }
    if ("name" in body) {
      if (String(body.name).length > 24) throw new ApiError("Name must be at most 24 characters.");
      upd.name = String(body.name).trim();
    }
    if ("domain_id" in body) {
      const d = body.domain_id === "" || body.domain_id == null ? null : Number(body.domain_id);
      if (d !== null && (!Number.isInteger(d) || d < 0 || d > 101)) throw new ApiError("ROS domain must be 0–101.");
      upd.domain_id = d;
    }
    if ("max_speed" in body) {
      const v = Number(body.max_speed);
      if (!(v >= 0.1 && v <= 1.0)) throw new ApiError("Max speed must be 0.1–1.0 m/s.");
      upd.max_speed = Math.round(v * 100) / 100;
      r.state.speed = upd.max_speed;
    }
    if ("priority" in body) {
      const p = Number(body.priority);
      if (!Number.isInteger(p) || p < -9 || p > 9) throw new ApiError("Priority must be -9 to 9.");
      upd.priority = p;
    }
    if ("notes" in body) upd.notes = String(body.notes).slice(0, 400);
    const before = r.info.id;
    Object.assign(r.info, upd, { configured: !!(upd.id || r.info.id) });
    if (upd.id && upd.id !== before) {
      r.say(before ? `Robot ID changed ${before} -> ${upd.id}.` : `Robot ID set to ${upd.id}.`);
      r.dock.name = `${upd.id} dock`;
    }
    if ("domain_id" in upd) r.say("ROS domain changed; it applies the next time the stack starts.");
    return { ok: true, robot: r.public() };
  }

  async launch(key, map) {
    await sleep(900);
    const r = this.get(key);
    if (!r.adopted) throw new ApiError("Add the robot to the fleet first.");
    const m = r.maps.find((x) => x.name === map);
    if (!m) throw new ApiError(`Map '${map}' not found.`);
    if (!m.has_posegraph) throw new ApiError(`Map '${map}' has no pose graph, so the robot can't localize on it.`);
    r.info.stack = { running: true, mode: "navigation", map, target: "real" };
    r.say(`Launching navigation on ${map} (real robot).`);
    this.event(`${r.label}: navigation starting on ${map}.`, key);
    return { ok: true };
  }

  async stopStack(key) {
    const r = this.get(key);
    this.handle(r, { action: "stop" });
    r.info.stack = { running: false, mode: null, map: null, target: null };
    this.event(`${r.label}: stack stopped.`, key);
    return { ok: true };
  }

  async command(key, cmd) {
    const r = this.get(key);
    if (!r.adopted) throw new ApiError("Add the robot to the fleet first.");
    this.handle(r, cmd);
    return { ok: true };
  }

  async commandAll(cmd) {
    const sent = this.fleet().map((r) => { this.handle(r, cmd); return r.machine; });
    this.event(`Fleet command '${cmd.action}' sent to ${sent.length} robot(s).`, null, cmd.action === "estop" ? "fault" : "info");
    return { ok: true, sent };
  }

  async dispatch(body) {
    const sites = Object.fromEntries([...this.layout.racks, ...this.layout.drop_zones].map((s) => [s.id, s]));
    const type = body.type || "transport";
    const src = sites[body.from_id], dst = sites[body.to_id];
    if (!dst || (type === "transport" && !src)) throw new ApiError("Pick sites from the fleet layout.");
    if (type === "transport" && src.state !== "occupied") throw new ApiError(`${src.name} has no rack to pick up.`);
    if (type === "transport" && dst.state === "occupied") throw new ApiError(`${dst.name} is already occupied.`);
    const target = type === "transport" ? src : dst;
    let pool = this.fleet().filter((r) => r.linked);
    let chosen, reason;
    if (body.robot && body.robot !== "auto") {
      chosen = pool.find((r) => r.machine === body.robot);
      if (!chosen) throw new ApiError("That robot can't take tasks now (offline or not navigating).");
      reason = "chosen by the operator";
    } else {
      const cands = pool.filter((r) => !r.state.carrying && r.state.state !== "ERROR").map((r) => {
        const p = r.state.robot_pose;
        const dist = Math.hypot(p.x - target.x, p.y - target.y);
        const busy = !["IDLE", "COMPLETED"].includes(r.state.state);
        return { r, dist, busy, cost: dist + 6 * r.state.queue.length + (busy ? 8 : 0) };
      }).sort((a, b) => a.cost - b.cost);
      if (!cands.length) throw new ApiError("No robot is free: all are offline, carrying or stopped.");
      const c = cands[0];
      chosen = c.r;
      reason = `${c.busy ? "queued behind its current task" : "idle"}, ${c.dist.toFixed(1)} m from ${target.name}`;
      if (cands[1]) reason += `; next best ${cands[1].r.label} (+${(cands[1].cost - c.cost).toFixed(1)} m)`;
    }
    const cmd = { action: "add_task", type, to_name: dst.name, to: dst };
    if (type === "transport") Object.assign(cmd, { rack_id: src.id, from_name: src.name, from: src });
    this.handle(chosen, cmd);
    if (["IDLE", "COMPLETED"].includes(chosen.state.state)) this.handle(chosen, { action: "start" });
    this.event(`Task ${type === "transport" ? `${src.name} → ` : ""}${dst.name} → ${chosen.label} (${reason}).`, chosen.machine);
    return { ok: true, robot: chosen.machine, label: chosen.label, reason };
  }

  /* ── robot setup over SSH (simulated; provision.py does it for real) ── */
  async provisionConnect({ host, user, password }) {
    await sleep(900);
    if (!host?.trim()) throw new ApiError("Type the Pi's IP address or hostname.");
    if (!password) throw new ApiError("Wrong user name or password.");
    return { ok: true, facts: { host: "ubuntu", os: "Ubuntu 24.04.1 LTS", codename: "noble", arch: "aarch64",
      home: `/home/${user || "ubuntu"}`, ros: "no", ws: "no", service: "inactive", mem: "7820", sudo: "yes" } };
  }

  async provisionRun(body) {
    const rid = String(body.robot_id || "").trim();
    if (!ID_RE.test(rid)) throw new ApiError("Robot ID: 1–16 letters, digits or '-' (e.g. AMR-03).");
    if (this.all().some((r) => (r.info.id || "").toLowerCase() === rid.toLowerCase())) throw new ApiError("That Robot ID is taken.");
    const steps = [["name", "Hostname and mDNS"], ["ros", "ROS 2 Jazzy"], ["upload", "Upload the workspace"],
      ["deps", "Install dependencies"], ["build", "Build (colcon)"], ["identity", "Robot ID and launcher service"],
      ["key", "Laptop key for later deploys"], ["password", "New login password"], ["check", "Robot answers on the network"]];
    const h = body.hostname;
    const script = [
      ["name", [`hostnamectl set-hostname ${h}`, "avahi-daemon enabled", `now ${h} / ${h}.local`]],
      ["ros", ["apt-get update", "Setting up ros2-apt-source (1.1.0)", "Setting up ros-jazzy-ros-base", "Setting up ros-dev-tools"]],
      ["upload", ["163 files, 7.6 MB from RaspberryPi_ROS2", `unpacked into /home/${body.user || "ubuntu"}/RaspberryPi_ROS2`]],
      ["deps", body.install_deps === false ? ["Skipped (already installed)."] : ["▸ Installing apt packages...", "▸ Adding user to dialout, i2c", "✓ Dependencies installed"]],
      ["build", ["Starting >>> amr_interfaces", "Finished <<< amr_interfaces", "Starting >>> amr_navigation", "Finished <<< amr_mission_control", "Summary: 7 packages finished"]],
      ["identity", [`Robot ID set to ${rid}.`, "Created symlink amr-launcher.service", "active"]],
      ["key", ["key installed"]],
      ["password", body.new_password ? ["Password changed. Use the new one from now on."] : ["Kept the current password."]],
      ["check", [`${rid} answers at ${body.host} and is in the fleet.`]],
    ];
    const job = { id: Math.random().toString(16).slice(2, 12), title: `Set up ${rid} at ${body.host}`, state: "running",
      error: null, steps: steps.map(([id, label]) => ({ id, label, state: "todo" })), lines: [], seq: 0, result: {}, started: now() };
    const log = (text, source = "pi") => { job.seq += 1; job.lines.push({ seq: job.seq, t: now(), source, text }); };
    this.jobs = { ...(this.jobs || {}), [job.id]: job };
    (async () => {
      log(`Connected to ubuntu (Ubuntu 24.04.1 LTS, aarch64).`, "job");
      for (const [sid, lines] of script) {
        const st = job.steps.find((s) => s.id === sid);
        st.state = "now";
        log(`▸ ${st.label}`, "step");
        for (const l of lines) { await sleep(700); log(l); }
        st.state = (sid === "deps" && body.install_deps === false) || (sid === "password" && !body.new_password) ? "skipped" : "done";
      }
      const r = new SimRobot({ machine: Math.random().toString(16).slice(2, 14), host: body.host, hostname: h, id: rid,
        name: body.name || "", domain_id: body.domain_id === "" || body.domain_id == null ? null : Number(body.domain_id), priority: 0,
        dock: { x: 8.2, y: 3.2 + ((this.robots.length - 3) % 4) * 0.9, yaw: 0 }, maps: {} }, this.siteLayout());
      r.info.stack = { running: false, mode: null, map: null, target: null };
      r.adopted = true;
      this.robots.push(r);
      r.say("Launcher service started; waiting for a map and navigation.");
      this.event(`${rid} set up over SSH and added to the fleet.`, r.machine);
      job.result = { key: r.machine, host: body.host, hostname: h, robot_id: rid };
      job.state = "done";
      log(`${rid} is set up. It appears in the fleet as ${h} (${h}.local).`, "step");
    })();
    return { ok: true, job: job.id };
  }

  async job(id, after = 0) {
    const j = this.jobs?.[id];
    if (!j) throw new ApiError("No such job.");
    return { ...j, steps: j.steps.map((s) => ({ ...s })), lines: j.lines.filter((l) => l.seq > after), last: j.seq };
  }

  async deploy(key) {
    const r = this.get(key);
    if (r.info.stack.running) throw new ApiError(`${r.label}'s stack is running. Stop it first: deploying restarts the launcher.`);
    const job = { id: Math.random().toString(16).slice(2, 12), title: `Deploy to ${r.label}`, state: "running", error: null,
      steps: [["upload", "Upload the workspace"], ["build", "Build (colcon)"], ["restart", "Restart the launcher"]].map(([id, label]) => ({ id, label, state: "todo" })),
      lines: [], seq: 0, result: {}, started: now() };
    const log = (text, source = "pi") => { job.seq += 1; job.lines.push({ seq: job.seq, t: now(), source, text }); };
    this.jobs = { ...(this.jobs || {}), [job.id]: job };
    (async () => {
      for (const [sid, lines] of [["upload", ["163 files, 7.6 MB", "unpacked"]], ["build", ["Summary: 7 packages finished"]], ["restart", ["active"]]]) {
        const st = job.steps.find((s) => s.id === sid);
        st.state = "now";
        log(`▸ ${st.label}`, "step");
        for (const l of lines) { await sleep(900); log(l); }
        st.state = "done";
      }
      job.state = "done";
      job.result = { key, host: r.host };
      r.say("Launcher restarted with the new code.");
      this.event(`New code deployed to ${r.label}.`, key);
      log("Deployed. The launcher restarted with the new code.", "step");
    })();
    return { ok: true, job: job.id };
  }

  setAutopilot(on) { this.autopilot = !!on; this.event(on ? "Demo traffic on: idle robots pick up random transports." : "Demo traffic off."); }

  warnings() {
    const out = [];
    const label = (k) => this.robots.find((r) => r.machine === k)?.label || "?";
    this.conflicts.filter((c) => c.kind === "blocked").forEach((c) => out.push({
      tone: "caution", robots: [c.a, c.b],
      text: `${label(c.a)} is stuck behind parked ${label(c.b)}. Send ${label(c.b)} to its dock or move it.`,
    }));
    this.conflicts.filter((c) => c.kind === "deadlock").forEach((c) => out.push({
      tone: "fault", robots: [c.a, c.b],
      text: `${label(c.a)} and ${label(c.b)} block each other. Drive one aside from its console, or stop one task.`,
    }));
    const ids = {};
    this.all().forEach((r) => { if (r.info.id) (ids[r.info.id.toLowerCase()] ||= []).push(r); });
    Object.values(ids).forEach((rs) => {
      if (rs.length > 1) out.push({ tone: "fault", robots: rs.map((r) => r.machine), text: `${rs.length} robots use the ID ${rs[0].info.id}. Give each its own ID.` });
    });
    const doms = {};
    this.fleet().forEach((r) => { (doms[String(r.info.domain_id)] ||= []).push(r); });
    Object.entries(doms).forEach(([d, rs]) => {
      if (rs.length > 1) out.push({ tone: "caution", robots: rs.map((r) => r.machine), text: `${rs.map((r) => r.label).join(", ")} share ROS domain ${d === "null" ? "(default)" : d}; their topics mix on shared Wi-Fi.` });
    });
    const maps = new Set(this.fleet().filter((r) => r.info.stack.running).map((r) => {
      const m = r.info.stack.map;
      return `${m}:${r.maps.find((x) => x.name === m)?.fingerprint}`;
    }));
    if (maps.size > 1) out.push({ tone: "caution", robots: this.fleet().map((r) => r.machine), text: "Robots run different maps (or different copies of one). Only robots on the same map are kept apart; sync the map from the Maps tab." });
    return out;
  }

  /* ── mission handling (mirrors mission_manager_node commands) ──────── */
  handle(r, cmd) {
    const s = r.state;
    switch (cmd.action) {
      case "add_task": {
        const task = { id: Math.random().toString(16).slice(2, 10), type: cmd.type, from: cmd.from, to: cmd.to,
          from_name: cmd.from_name, to_name: cmd.to_name, rack_id: cmd.rack_id, status: "queued" };
        s.queue = [...s.queue, task];
        r.say(`Task added: ${task.from_name ? `${task.from_name} → ` : ""}${task.to_name}`);
        break;
      }
      case "start":
        if (s.state === "IDLE" || s.state === "COMPLETED") { r.say(`Mission started with ${s.queue.length} task(s).`); this.nextTask(r); }
        break;
      case "pause":
        if (r.exec && s.state !== "PAUSED") { r.resume = s.state; s.state = "PAUSED"; r.say("Mission paused."); }
        break;
      case "resume":
        if (s.state === "PAUSED") { s.state = r.resume || "RUNNING"; r.say("Mission resumed."); }
        break;
      case "stop":
        if (r.exec?.task) r.exec.task.status = "queued";
        r.exec = null; r.plan = [];
        Object.assign(s, { state: "IDLE", current_task: null, current_step: "", progress: 0 });
        r.say("Mission stopped.");
        break;
      case "estop":
        r.exec = null; r.plan = [];
        Object.assign(s, { state: "ERROR", current_task: null, current_step: "", progress: 0 });
        r.say("EMERGENCY STOP ACTIVATED");
        break;
      case "estop_release":
        if (s.state === "ERROR") { s.state = "IDLE"; r.say("E-stop released."); }
        break;
      case "return_to_dock":
        if (s.state === "ERROR") break;
        if (r.exec?.task) r.exec.task.status = "queued";
        r.say("Returning to dock...");
        this.startDrive(r, { kind: "drive", step: "", goal: r.dock, state: "RETURNING" }, null);
        break;
      case "set_speed":
        s.speed = Math.max(0.1, Math.min(1, +cmd.speed || 0.4));
        r.say(`Max speed set to ${s.speed.toFixed(2)} m/s`);
        break;
      case "remove_task":
        s.queue = s.queue.filter((t) => t.id !== cmd.id || t.status === "running");
        r.say(`Task removed: ${cmd.id}`);
        break;
      case "reorder_tasks": {
        const byId = Object.fromEntries(s.queue.map((t) => [t.id, t]));
        s.queue = (cmd.order || []).map((id) => byId[id]).filter(Boolean);
        r.say("Task queue reordered.");
        break;
      }
      case "navigate_to":
        s.queue = [{ id: Math.random().toString(16).slice(2, 10), type: "navigate", from_name: "Current",
          to_name: cmd.name || `(${(+cmd.x).toFixed(1)}, ${(+cmd.y).toFixed(1)})`,
          to: { x: +cmd.x, y: +cmd.y, yaw: +cmd.yaw || 0 }, status: "queued" }, ...s.queue];
        if (s.state === "IDLE" || s.state === "COMPLETED") this.nextTask(r);
        break;
      // Layout edits from a robot's console change the fleet layout, so every
      // robot keeps the same lanes, racks and zones (as fleet_server.py does).
      case "update_locations": {
        const prev = Object.fromEntries([...this.layout.racks, ...this.layout.drop_zones].map((l) => [l.id, l.state]));
        const keep = (arr, def) => (arr || []).map((it) => ({ ...it, state: it.state || prev[it.id] || def }));
        this.commitLayout({ racks: keep(cmd.racks, "occupied"), drop_zones: keep(cmd.drop_zones, "free") }, r);
        break;
      }
      case "update_lanes":
        this.commitLayout({ lanes: cmd.lanes || [] }, r);
        break;
      case "update_dock":
        r.dock = { x: +cmd.x, y: +cmd.y, yaw: +cmd.yaw || 0, name: cmd.name || r.dock.name };
        s.locations = { ...s.locations, dock: r.dock };
        r.say(`Dock updated: ${r.dock.name}.`);
        break;
      case "set_location_state":
        this.setSite(cmd.id, cmd.state, r);
        break;
      case "teleop":
        r.teleop = { lx: +cmd.lx || 0, az: +cmd.az || 0, until: performance.now() + 350 };
        break;
      case "lift":
        r.say(`Lift commanded to ${Number(cmd.height || 0).toFixed(2)}m`);
        break;
      default:
        r.say(`Unknown command: ${cmd.action}`);
    }
  }

  nextTask(r) {
    const s = r.state;
    const task = s.queue[0];
    if (!task) {
      s.current_task = null;
      r.say("All tasks complete. Returning to dock...");
      return this.startDrive(r, { kind: "drive", step: "", goal: r.dock, state: "RETURNING" }, null);
    }
    task.status = "running";
    s.current_task = task;
    const steps = task.type === "transport"
      ? [
          { kind: "drive", step: "navigating_to_rack", goal: task.from, state: "NAVIGATING" },
          { kind: "wait", step: "centering", dur: 1.2, state: "NAVIGATING" },
          { kind: "wait", step: "lifting", dur: 2.0, state: "LIFTING", done: () => { s.carrying = true; this.setSite(task.from.id, "free", r); } },
          { kind: "drive", step: "navigating_to_dest", goal: task.to, state: "NAVIGATING" },
          { kind: "wait", step: "inserting", dur: 1.2, state: "NAVIGATING" },
          { kind: "wait", step: "dropping", dur: 2.0, state: "DROPPING", done: () => { s.carrying = false; this.setSite(task.to.id, "occupied", r); } },
        ]
      : [{ kind: "drive", step: "navigating", goal: task.to, state: "NAVIGATING" }];
    r.say(`Executing: ${task.from_name ? `${task.from_name} → ` : ""}${task.to_name}`);
    r.exec = { task, steps, idx: -1 };
    this.advance(r);
  }

  startDrive(r, step, task) {
    r.exec = { task, steps: [step], idx: -1 };
    r.state.current_task = task;
    this.advance(r);
  }

  advance(r) {
    const ex = r.exec, s = r.state;
    ex.idx += 1;
    if (ex.idx >= ex.steps.length) return this.finish(r);
    const st = ex.steps[ex.idx];
    Object.assign(s, { current_step: st.step, state: st.state, progress: 0 });
    ex.t = 0;
    if (st.kind === "drive") {
      ex.path = this.route(r, st.goal);
      ex.wp = 1;
      ex.total = pathLength(ex.path);
      r.plan = ex.path;
      if (st.step === "navigating_to_rack") r.say(`Navigating to ${ex.task.from_name}...`);
      if (st.step === "navigating_to_dest") r.say(`Carrying rack to ${ex.task.to_name}...`);
    } else {
      r.plan = [];
      if (st.step === "lifting") r.say("Lifting rack...");
      if (st.step === "dropping") r.say("Lowering rack...");
    }
    return undefined;
  }

  finish(r) {
    const s = r.state, ex = r.exec;
    r.plan = [];
    if (!ex.task) {
      r.exec = null;
      Object.assign(s, { state: "IDLE", current_step: "", progress: 0 });
      return r.say("Docked. Mission complete.");
    }
    s.queue = s.queue.filter((t) => t.id !== ex.task.id);
    r.say(`Task complete: ${ex.task.to_name}`);
    return this.nextTask(r);
  }

  commitLayout(patchLayout, by) {
    Object.assign(this.layout, clone(patchLayout));
    this.layout.rev += 1;
    this.layout.source = by.label;
    this.layout.updated = now();
    this.fleet().forEach((o) => { o.state.locations = { dock: o.dock, ...this.siteLayout() }; });
    this.event(`Layout rev ${this.layout.rev} from ${by.label}'s console: ${this.layout.lanes.length} lanes, ${this.layout.racks.length} racks, ${this.layout.drop_zones.length} zones. Sent to every robot.`, by.machine);
  }

  /** Planner grid with the other robots that are standing still stamped in,
   *  as the robot's own costmap would see them (fleet_layer + lidar). */
  obstacleGrid(r) {
    if (!this.truth) return null;
    const still = this.fleet().filter((o) => o !== r && !isDriving(this.missionOf(o)) || (o !== r && o.hold));
    if (!still.length) return this.truth;
    const g = cloneGrid(this.truth);
    for (const o of still) {
      const p = o.state.robot_pose;
      const rad = radiusOf(o.state) + 0.25;
      for (let y = p.y - rad; y <= p.y + rad; y += g.resolution) {
        for (let x = p.x - rad; x <= p.x + rad; x += g.resolution) {
          if (Math.hypot(x - p.x, y - p.y) > rad) continue;
          const i = cellIndex(g, x, y);
          if (i >= 0) g.data[i] = 100;
        }
      }
    }
    touch(g);
    return g;
  }

  /**
   * With lanes the robot drives on them and nowhere else, as
   * mission_manager does: straight back out of a slot or the dock along its
   * own axis, the lane to the goal's foot, then straight in. Points marked
   * `rev` are driven in reverse. Without lanes: a free plan.
   */
  route(r, goal) {
    const pose = r.state.robot_pose;
    const lanes = this.layout.lanes || [];
    if (!lanes.length) return planPath(this.obstacleGrid(r), pose, goal);
    const out = [{ x: pose.x, y: pose.y }];
    let start = nearestLanePoint(lanes, pose);
    if (start.d > 0.3) {
      const back = approachFoot(lanes, { x: pose.x, y: pose.y, yaw: pose.yaw });
      if (back) start = back;
      out.push({ x: start.x, y: start.y, rev: true });
    }
    const foot = approachFoot(lanes, goal) || nearestLanePoint(lanes, goal);
    const along = lanePath(lanes, start, foot, this.layout.lane_corner_radius) || [start, foot];
    out.push(...along.slice(1), { x: goal.x, y: goal.y });
    return out;
  }

  setSite(id, state, by) {
    for (const list of [this.layout.racks, this.layout.drop_zones]) {
      const it = list.find((x) => x.id === id);
      if (it) it.state = state;
    }
    for (const r of this.fleet()) {
      for (const key of ["racks", "drop_zones"]) {
        const it = r.state.locations[key].find((x) => x.id === id);
        if (it) it.state = state;
      }
    }
    const name = [...this.layout.racks, ...this.layout.drop_zones].find((x) => x.id === id)?.name || id;
    this.event(`${name} is now ${state} (reported by ${by.label}).`, by.machine);
  }

  /** Centre distance at which two robots' footprints touch (with a margin). */
  contact(r, o) {
    const half = (x) => (x?.state?.carrying ? 0.53 : 0.36);
    return half(r) + (o ? half(o) : 0.36) + 0.12;
  }

  missionOf(r) {
    return { state: r.state.state, step: r.state.current_step, carrying: r.state.carrying, speed: r.state.speed, task: r.state.current_task };
  }

  /* ── simulation ─────────────────────────────────────────────────── */
  tick(dt) {
    const t = now();
    if (t - this.lastTraffic >= 0.2) {
      this.lastTraffic = t;
      const bots = this.fleet().map((r) => {
        r.key = r.machine;
        r.priority = r.info.priority;
        r.carrying = r.state.carrying;
        r.mission = this.missionOf(r);
        r.pose = r.state.robot_pose;
        r.map = `${r.info.stack.map}:${r.maps.find((m) => m.name === r.info.stack.map)?.fingerprint}`;
        return r;
      });
      const sites = [...this.layout.racks, ...this.layout.drop_zones];
      const { conflicts, events } = trafficTick(bots, t, this.trafficMem, this.layout.lanes || [], sites);
      this.conflicts = conflicts;
      for (const e of events) {
        this.event(e.text, e.robot, e.tone);
        const r = bots.find((b) => b.key === e.robot);
        if (r && e.tone !== "fault") r.say(r.hold ? `Holding for ${r.hold.by_label}: ${r.hold.reason}.` : "Way is clear; continuing.");
      }
      // No lanes only: stopped in front of a robot for a while, plan once
      // around it like Nav2 would. With lanes a robot never leaves them.
      for (const r of (this.layout.lanes || []).length ? [] : bots) {
        if (!r.blockedSince || t - r.blockedSince < 2 || t - r.replannedAt < 8 || !r.exec?.path) continue;
        r.replannedAt = t;
        const st = r.exec.steps[r.exec.idx];
        const detour = planPath(this.obstacleGrid(r), r.state.robot_pose, st.goal);
        const others = this.fleet().filter((o) => o !== r).map((o) => [o.state.robot_pose.x, o.state.robot_pose.y]);
        const clear = others.every(([ox, oy]) => detour.slice(1, 8).every((p) => Math.hypot(p.x - ox, p.y - oy) > this.contact(r, null)));
        if (clear) {
          r.exec.path = detour;
          r.exec.wp = 1;
          r.plan = detour;
          r.say("Planning around a robot in the way.");
        }
      }
    }
    if (this.autopilot && t - this.lastAuto > 1.5) {
      this.lastAuto = t;
      this.autoDispatch();
    }
    for (const r of this.fleet()) this.step(r, dt);
  }

  autoDispatch() {
    const idle = this.fleet().filter((r) => r.state.state === "IDLE" && !r.state.queue.length && r.info.stack.running);
    if (!idle.length || Math.random() < 0.4) return;
    const reserved = new Set(this.fleet().flatMap((r) => r.state.queue.flatMap((q) => [q.from?.id, q.to?.id])));
    const sites = [...this.layout.racks, ...this.layout.drop_zones].filter((s) => !reserved.has(s.id));
    const full = sites.filter((s) => s.state === "occupied");
    const empty = sites.filter((s) => s.state !== "occupied");
    if (!full.length || !empty.length) return;
    const from = full[Math.floor(Math.random() * full.length)];
    const to = empty[Math.floor(Math.random() * empty.length)];
    this.dispatch({ type: "transport", from_id: from.id, to_id: to.id, robot: "auto" })
      .catch((e) => console.warn("[FleetPreview] demo dispatch failed:", e));
  }

  step(r, dt) {
    const ex = r.exec, s = r.state;
    if (!ex && r.teleop && performance.now() < r.teleop.until && s.state !== "ERROR") {
      const p = s.robot_pose;
      p.yaw = wrap(p.yaw + r.teleop.az * dt);
      p.x += Math.cos(p.yaw) * r.teleop.lx * dt;
      p.y += Math.sin(p.yaw) * r.teleop.lx * dt;
      return;
    }
    if (!ex || s.state === "PAUSED" || s.state === "ERROR") return;
    const st = ex.steps[ex.idx];
    if (st.kind === "wait") {
      ex.t += dt;
      s.progress = Math.min(1, ex.t / st.dur);
      if (ex.t >= st.dur) { st.done?.(); this.advance(r); }
      return;
    }
    // Fleet back-off: reverse along the lane (then straight into a refuge), then hold.
    const rt = r.hold?.retreat;
    if (rt && r.retreatDone !== rt.id) {
      if (r.retreatRun?.id !== rt.id) {
        // Straight back the way it faces from: reverse when the way off lies behind it.
        const end = rt.path[Math.min(rt.path.length - 1, 3)];
        const behind = (end.x - s.robot_pose.x) * Math.cos(s.robot_pose.yaw) + (end.y - s.robot_pose.y) * Math.sin(s.robot_pose.yaw) < 0;
        r.retreatRun = { id: rt.id, wp: 1, path: [
          { x: s.robot_pose.x, y: s.robot_pose.y },
          ...rt.path.map((p) => ({ x: p.x, y: p.y, rev: behind })),
          ...(rt.into ? [{ x: rt.into.x, y: rt.into.y }] : []),
        ] };
        r.say(rt.into ? `Backing off into ${rt.into.name} to let ${r.hold.by_label} pass.` : `Backing off along the lane to let ${r.hold.by_label} pass.`);
      }
      if (this.drivePath(r, r.retreatRun, dt)) r.retreatDone = rt.id;
      r.retreated = true;
      return;
    }
    if (r.hold) { r.blockedSince = null; return; } // fleet hold: stopped in place, same goal kept
    if (r.retreated && st.kind === "drive") {
      // Back on the way after backing off: route again from where it is now.
      r.retreated = false;
      ex.path = this.route(r, st.goal);
      ex.wp = 1;
      ex.total = pathLength(ex.path);
    }
    const pose = s.robot_pose, path = ex.path, turn = 2.2;
    // Its own costmap: don't drive (or turn) into another robot; stop and wait.
    if (ex.wp < path.length) {
      const tgt = path[ex.wp];
      const dd = Math.hypot(tgt.x - pose.x, tgt.y - pose.y) || 1;
      const ahead = { x: pose.x + ((tgt.x - pose.x) / dd) * 0.35, y: pose.y + ((tgt.y - pose.y) / dd) * 0.35 };
      const inWay = this.fleet().some((o) => {
        if (o === r) return false;
        const q = o.state.robot_pose;
        const now = Math.hypot(q.x - pose.x, q.y - pose.y);
        const next = Math.hypot(q.x - ahead.x, q.y - ahead.y);
        return next < this.contact(r, o) && next < now;
      });
      if (inWay) { r.blockedSince = r.blockedSince || now(); return; }
    }
    r.blockedSince = null;
    if (ex.wp >= path.length) {
      const err = wrap((st.goal.yaw || 0) - pose.yaw);
      if (Math.abs(err) < 0.03) { pose.yaw = st.goal.yaw || 0; s.progress = 1; this.advance(r); return; }
      pose.yaw = wrap(pose.yaw + Math.sign(err) * Math.min(Math.abs(err), turn * dt));
      return;
    }
    this.drivePath(r, ex, dt);
    const remaining = pathLength([{ x: pose.x, y: pose.y }, ...path.slice(ex.wp)]);
    s.progress = ex.total > 0 ? Math.max(0, Math.min(1, 1 - remaining / ex.total)) : 1;
  }

  /** One tick along run.path (run.wp = next point). Turns on the spot when
   *  the heading is off, drives `rev` points in reverse. true at the end. */
  drivePath(r, run, dt) {
    const s = r.state, pose = s.robot_pose, path = run.path, turn = 2.2;
    if (run.wp >= path.length) return true;
    const target = path[run.wp];
    const dx = target.x - pose.x, dy = target.y - pose.y, dist = Math.hypot(dx, dy);
    if (dist < 0.06) { run.wp += 1; return run.wp >= path.length; }
    const heading = Math.atan2(dy, dx) + (target.rev ? Math.PI : 0);
    const err = wrap(heading - pose.yaw);
    pose.yaw = wrap(pose.yaw + Math.sign(err) * Math.min(Math.abs(err), turn * dt));
    if (Math.abs(err) < 0.25) {
      const v = s.speed * (s.carrying ? 0.8 : 1) * (target.rev ? 0.6 : 1);
      const stepLen = Math.min(dist, v * dt * Math.max(0.25, Math.cos(err)));
      pose.x += (dx / dist) * stepLen;
      pose.y += (dy / dist) * stepLen;
    }
    r.plan = [{ x: pose.x, y: pose.y }, ...path.slice(run.wp)];
    return false;
  }
}

/**
 * One fleet robot seen through the single-robot console (Preview): the same
 * simulated robot the fleet view shows, with the fleet's layout and map. It
 * has the interface useMission expects of PreviewEngine.
 */
export class FleetConsole {
  constructor(fleet, host) {
    this.fleet = fleet;
    this.host = host;
    this.listeners = new Set();
    this.timer = null;
  }

  get robot() { return this.fleet.all().find((r) => r.host === this.host) || null; }
  get online() { const r = this.robot; return !!(r && r.adopted && r.info.stack.running); }

  subscribe(fn) {
    this.listeners.add(fn);
    fn(this.snapshot());
    if (!this.timer) this.timer = setInterval(() => { const s = this.snapshot(); this.listeners.forEach((l) => l(s)); }, 100);
    return () => {
      this.listeners.delete(fn);
      if (!this.listeners.size) { clearInterval(this.timer); this.timer = null; }
    };
  }

  snapshot() {
    const r = this.robot;
    if (!r) return { online: false, starting: false, missionState: DEFAULT_STATE, feedbackLog: [], plan: [] };
    const s = r.state, l = this.fleet.layout;
    return {
      online: this.online,
      starting: false,
      missionState: {
        ...DEFAULT_STATE, ...s,
        robot_pose: { ...s.robot_pose },
        queue: s.queue.map((t) => ({ ...t })),
        locations: { dock: { ...r.dock }, racks: l.racks, drop_zones: l.drop_zones, lanes: l.lanes },
        lane_corner_radius: l.lane_corner_radius,
        fleet: { managed: true, held: !!r.hold, by: r.hold?.by_label || "", reason: r.hold?.reason || "" },
      },
      feedbackLog: r.log,
      plan: r.plan,
    };
  }

  send(cmd) {
    const r = this.robot;
    if (r && this.online) this.fleet.handle(r, cmd);
  }

  get api() {
    if (this._api) return this._api;
    const c = this, f = this.fleet;
    const meta = () => (f.truth ? { resolution: f.truth.resolution, origin: [f.truth.origin.x, f.truth.origin.y], width: f.truth.width, height: f.truth.height,
      negate: 0, occupied_thresh: 0.65, free_thresh: 0.196 } : {});
    const fleetOnly = async () => { throw new Error("In Preview this robot is part of the simulated fleet: manage its maps from the Fleet › Maps tab."); };
    this._api = {
      kind: "fleet-preview",
      status: async () => {
        await f.ready;
        const r = c.robot;
        const st = r?.info.stack || {};
        return { version: 2, running: !!st.running, target: st.target, mode: st.mode, map: st.map, started_at: f.started,
          rviz: { running: false, error: "Preview: no RViz." }, display: false, last_exit: null,
          default_map: r?.info.default_map || "Hang1", maps_dir: "~/AMR/src/amr_navigation/maps", log_seq: r?.log.length || 0 };
      },
      logs: async (after) => {
        const lines = (c.robot?.log || []).map((l, i) => ({ seq: i + 1, t: l.timestamp, source: "mission", text: l.message }));
        return { lines: lines.filter((l) => l.seq > (after || 0)), last: lines.length };
      },
      launch: ({ map }) => f.launch(c.robot.machine, map),
      stop: () => f.stopStack(c.robot.machine),
      rviz: async () => { throw new Error("Preview has no RViz."); },
      maps: async () => {
        await f.ready;
        const r = c.robot;
        return { maps: (r?.maps || []).map((m) => ({ ...m, ...meta(), has_image: true, edited: false, modified: f.started - 86400 })),
          default: r?.info.default_map || "Hang1", in_use: r?.info.stack.running ? r.info.stack.map : null, dir: "~/AMR/src/amr_navigation/maps" };
      },
      pgm: () => f.pgm(),
      putPgm: fleetOnly, saveMap: fleetOnly, rename: fleetOnly, duplicate: fleetOnly,
      setDefault: fleetOnly, revert: fleetOnly, remove: fleetOnly, restore: fleetOnly,
    };
    return this._api;
  }
}
