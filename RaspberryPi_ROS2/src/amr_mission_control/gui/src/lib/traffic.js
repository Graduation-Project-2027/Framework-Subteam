/**
 * Fleet traffic rules, mirrored from fleet_server.py (_claim, _traffic_tick)
 * so Preview behaves exactly like the laptop's fleet manager.
 *
 * Every driving robot claims the next few metres of its path. Two claims
 * closer than both bounding radii plus CLEARANCE conflict; the lower-ranked
 * robot holds until the conflict has stayed clear for RELEASE_AFTER. A parked
 * robot in the way is not a conflict: Nav2 plans around it.
 *
 * In the way goes first: when exactly one robot stands in the other's
 * path (a follower behind a leader, a robot waiting on a lane another must
 * drive along), it goes first whatever the priority, so nobody is held
 * behind a robot that is itself held.
 *
 * Head-on on a lane (each stands in the other's path): the lower-ranked
 * robot backs off along the lane, in reverse, to the nearest spot clear of
 * the other's whole remaining route, and holds there until it has passed.
 *
 * Hold loops (A waits for B, B for C, C for A) are never solved pair by
 * pair: one robot in the loop gets right of way for BREAK_FOR seconds,
 * the highest-ranked whose path is not physically blocked, and the others
 * hold for it.
 *
 * Deadlock breaker: when the robot with right of way has not moved for
 * STUCK_AFTER because the held one stands in its path, the order flips so
 * the held robot clears the way. If that does not get anyone moving either,
 * the pair is reported as a deadlock for the operator.
 */
export const LOOKAHEAD_T = 5.0;
export const LOOKAHEAD_MIN = 1.2;
export const LOOKAHEAD_MAX = 4.0;
export const CLEARANCE = 0.15;
export const RELEASE_AFTER = 1.0;
export const STUCK_AFTER = 4.0;
export const BREAK_FOR = 6.0;
export const ROBOT_R = Math.hypot(0.35, 0.32);
export const CARRY_R = Math.hypot(0.525, 0.525);

import { buildGraph } from "./lanes";

const TRAVEL_STEPS = new Set(["navigating", "navigating_to_rack", "navigating_to_dest", ""]);
const TRAVEL_STATES = new Set(["RUNNING", "NAVIGATING", "RETURNING"]);

export const radiusOf = (m) => (m?.carrying ? CARRY_R : ROBOT_R);

export function isDriving(m) {
  return !!m && TRAVEL_STATES.has(m.state) && TRAVEL_STEPS.has(m.step || m.current_step || "");
}

/** Path ahead (points 0.15 m apart) the robot will cover in LOOKAHEAD_T. */
export function claimOf(pose, plan, m) {
  if (!pose) return [];
  const pts = [[pose.x, pose.y]];
  if (!isDriving(m) || !plan || plan.length < 2) return pts;
  const reach = Math.max(LOOKAHEAD_MIN, Math.min(LOOKAHEAD_MAX, (m.speed || 0.4) * LOOKAHEAD_T));
  let k = 0, best = Infinity;
  plan.forEach((p, i) => {
    const d = (p.x - pose.x) ** 2 + (p.y - pose.y) ** 2;
    if (d < best) { best = d; k = i; }
  });
  let acc = 0, last = pts[0];
  for (const p of plan.slice(k)) {
    const d = Math.hypot(p.x - last[0], p.y - last[1]);
    if (d < 0.15) continue;
    acc += d;
    last = [p.x, p.y];
    pts.push(last);
    if (acc >= reach) break;
  }
  return pts;
}

export function gap(a, b) {
  let g = Infinity;
  for (const p of a) for (const q of b) g = Math.min(g, Math.hypot(p[0] - q[0], p[1] - q[1]));
  return g;
}

/** Higher goes first: operator priority, carrying, first to start driving. */
function outranks(a, b) {
  const ka = [a.priority || 0, a.carrying ? 1 : 0, -(a.movingSince || Infinity)];
  const kb = [b.priority || 0, b.carrying ? 1 : 0, -(b.movingSince || Infinity)];
  for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] > kb[i];
  return a.label > b.label;
}

/**
 * One controller tick. `bots`: [{ key, label, priority, carrying, mission,
 * pose, plan, map, hold, clearSince, movingSince }] mutated in place (claim,
 * hold, clearSince, movingSince, lastPos, lastMoveAt). `mem` keeps flipped
 * pairs between ticks. Returns { conflicts, events }.
 */
export function createTrafficMemory() {
  return { flipped: new Map(), retreats: new Map(), breakers: new Map(), failed: new Map() };
}

const dist2 = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);

function projectSeg(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const L = dx * dx + dy * dy;
  const t = L < 1e-9 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L));
  const q = { x: a.x + t * dx, y: a.y + t * dy };
  return { ...q, d: dist2(p, q) };
}

/** The part of a robot's plan still ahead of it, as [x, y] points. */
export function planAhead(r) {
  const plan = r.plan || [];
  if (!plan.length) return [];
  let k = 0, best = Infinity;
  plan.forEach((p, i) => { const d = (p.x - r.pose.x) ** 2 + (p.y - r.pose.y) ** 2; if (d < best) { best = d; k = i; } });
  return plan.slice(k).map((p) => [p.x, p.y]);
}

/** Where a site's face meets the lane: the ray from its centre along its
 *  approach direction (opposite its yaw), else the closest perpendicular
 *  foot on that side. Mirrors mission_manager _find_lane_approach_foot. */
export function approachFoot(lanes, site) {
  const ax = -Math.cos(site.yaw || 0), ay = -Math.sin(site.yaw || 0);
  let hit = null;
  for (const l of lanes || []) {
    for (let s = 0; s < l.points.length - 1; s++) {
      const A = l.points[s], B = l.points[s + 1];
      const ex = B.x - A.x, ey = B.y - A.y;
      const den = ax * ey - ay * ex;
      if (Math.abs(den) < 1e-9) continue;
      const t = ((A.x - site.x) * ey - (A.y - site.y) * ex) / den;
      const u = ((A.x - site.x) * ay - (A.y - site.y) * ax) / den;
      if (t > 0.05 && u >= -1e-6 && u <= 1 + 1e-6 && (!hit || t < hit.score)) {
        hit = { x: site.x + ax * t, y: site.y + ay * t, score: t, ray: true };
      }
    }
  }
  if (hit) return hit;
  let best = null;
  for (const l of lanes || []) {
    for (let s = 0; s < l.points.length - 1; s++) {
      const f = projectSeg(site, l.points[s], l.points[s + 1]);
      const side = (f.x - site.x) * ax + (f.y - site.y) * ay >= 0;
      const score = side ? f.d : f.d + 1000;
      if (!best || score < best.score) best = { x: f.x, y: f.y, score };
    }
  }
  return best && best.score < 1000 ? best : null;
}

/**
 * How `lo` gets out of `hi`'s way, or null.
 *  1. Back off along the lane (in reverse) to the nearest point clear of
 *     hi's body and its whole route ahead, and of every other robot.
 *  2. If the lane can't clear it: a refuge. Back off to a site's lane foot
 *     and go straight in: under an occupied rack when not carrying (the
 *     robot fits under racks), into a free location when carrying. Sites in
 *     another robot's task, or with a robot in them, are never used.
 * In a spur (between a site and its lane foot): back up the spur, toward
 * the site, to the first point clear of hi (only into a site it may enter).
 * Returns { path, into?: site } with path points along the lane or spur.
 */
export function planRetreat(lanes, lo, hi, reach, bots, sites = []) {
  const { nodes, adj } = buildGraph(lanes);
  let best = null;
  nodes.forEach((n, i) => adj[i].forEach((e) => {
    if (e.j < i) return;
    const f = projectSeg(lo.pose, n, nodes[e.j]);
    if (!best || f.d < best.f.d) best = { i, j: e.j, f };
  }));
  const route = [[hi.pose.x, hi.pose.y], ...planAhead(hi)];
  if (!best || best.f.d > 0.3) {
    // In a spur: the site whose straight approach the robot is standing on.
    const site = sites.find((s) => {
      const foot = approachFoot(lanes, s);
      if (!foot) return false;
      const f = projectSeg(lo.pose, s, foot);
      return f.d < 0.2;
    });
    if (!site || (lo.mission?.carrying && site.state === "occupied")) return null;
    const d = dist2(lo.pose, site);
    const pts = [{ x: lo.pose.x, y: lo.pose.y }];
    for (let t = 0.1; t <= d; t += 0.1) {
      const p = { x: lo.pose.x + ((site.x - lo.pose.x) * t) / d, y: lo.pose.y + ((site.y - lo.pose.y) * t) / d };
      pts.push(p);
      if (dist2(p, hi.pose) > reach + 0.3 && gap(route, [[p.x, p.y]]) > reach + 0.1) return { path: pts, spur: true };
    }
    return { path: [...pts, { x: site.x, y: site.y }], spur: true };
  }
  const others = bots.filter((b) => b !== lo && b !== hi);
  const blockedAt = (p) => dist2(p, hi.pose) < reach || others.some((o) => dist2(p, o.pose) < reach);
  const clearOfRoute = (p, margin) => dist2(p, hi.pose) > reach + margin && gap(route, [[p.x, p.y]]) > reach + 0.1;

  // Refuges this robot may use, with the lane foot it enters from.
  const reserved = new Set(bots.flatMap((b) => [b.mission?.task?.from?.id, b.mission?.task?.to?.id, b.mission?.task?.rack_id]).filter(Boolean));
  const refuges = sites.filter((s) => {
    if (reserved.has(s.id)) return false;
    if (lo.mission?.carrying && s.state === "occupied") return false;
    if (bots.some((b) => b !== lo && dist2(b.pose, s) < reach)) return false;
    return clearOfRoute(s, 0.3);
  }).map((s) => ({ site: s, foot: approachFoot(lanes, s) })).filter((r) => r.foot);

  // Walk the lane from lo's spot; stop(p) says when the walk has arrived.
  const walk = (toward, from, stop) => {
    const pts = [{ x: best.f.x, y: best.f.y }];
    let cur = pts[0], target = toward, prev = from, total = 0;
    while (total < 12) {
      const n = nodes[target];
      const d = dist2(cur, n);
      for (let t = 0.1; t <= d; t += 0.1) {
        const p = { x: cur.x + ((n.x - cur.x) * t) / d, y: cur.y + ((n.y - cur.y) * t) / d };
        if (blockedAt(p)) return null;
        pts.push(p);
        total += 0.1;
        const hit = stop(p);
        if (hit) return { path: pts, ...hit };
      }
      cur = { x: n.x, y: n.y };
      // At a junction take the branch leading furthest from the other robot.
      const next = adj[target].filter((e) => e.j !== prev)
        .sort((e1, e2) => dist2(nodes[e2.j], hi.pose) - dist2(nodes[e1.j], hi.pose))[0];
      if (!next) return null;
      prev = target;
      target = next.j;
    }
    return null;
  };
  const shortest = (opts) => opts.filter(Boolean).sort((p, q) => p.path.length - q.path.length)[0] || null;
  const onLane = (p) => (clearOfRoute(p, 0.3) ? {} : null);
  const intoRefuge = (p) => {
    const r = refuges.find((x) => dist2(p, x.foot) < 0.08);
    return r ? { into: r.site } : null;
  };
  return shortest([walk(best.j, best.i, onLane), walk(best.i, best.j, onLane)])
    || shortest([walk(best.j, best.i, intoRefuge), walk(best.i, best.j, intoRefuge)]);
}

export function trafficTick(bots, now, mem = createTrafficMemory(), lanes = [], sites = []) {
  const events = [];
  for (const b of bots) {
    b.claim = claimOf(b.pose, b.plan, b.mission);
    b.movingSince = isDriving(b.mission) ? (b.movingSince || now) : null;
    if (!b.lastPos || Math.hypot(b.pose.x - b.lastPos[0], b.pose.y - b.lastPos[1]) > 0.1) {
      b.lastPos = [b.pose.x, b.pose.y];
      b.lastMoveAt = now;
    }
  }
  const stuck = (r, since = 0) => now - Math.max(r.lastMoveAt ?? now, since) > STUCK_AFTER;
  const seen = new Set();
  const goNow = new Set(); // winners of a flip: their hold lifts without the release delay
  const conflicts = [];
  const wants = new Map();
  const handled = new Set();
  // Robots backing off: held (with their back-off path) until the robot they
  // made way for no longer drives past the spot they left, or themselves.
  for (const [pair, rt] of mem.retreats) {
    const lo = bots.find((b) => b.key === rt.loser), hi = bots.find((b) => b.key === rt.winner);
    if (!lo || !hi) { mem.retreats.delete(pair); continue; }
    const reach = radiusOf(lo.mission) + radiusOf(hi.mission) + CLEARANCE;
    const ahead = [[hi.pose.x, hi.pose.y], ...planAhead(hi)];
    const inTheWay = isDriving(hi.mission) && gap(ahead, [rt.from, [lo.pose.x, lo.pose.y]]) < reach;
    if (inTheWay) {
      handled.add(pair);
      seen.add(pair);
      wants.set(lo.key, { winner: hi, reason: `backing off for ${hi.label}`, retreat: rt });
      conflicts.push({ a: hi.key, b: lo.key, kind: "yield", reason: "backing off to let it pass", gap: 0 });
    } else {
      mem.retreats.delete(pair);
      goNow.add(lo.key);
    }
  }
  for (let i = 0; i < bots.length; i++) {
    for (let j = i + 1; j < bots.length; j++) {
      const a = bots[i], b = bots[j];
      if (a.map !== b.map) continue;
      const reach = radiusOf(a.mission) + radiusOf(b.mission) + CLEARANCE;
      const g = gap(a.claim, b.claim);
      if (g >= reach) continue;
      const da = isDriving(a.mission), db = isDriving(b.mission);
      if (!da && !db) continue;
      if (handled.has([a.key, b.key].sort().join("|"))) continue;
      if (da && db) {
        let [lo, hi] = outranks(a, b) ? [b, a] : [a, b];
        let reason = gap(lo.claim.slice(0, 1), hi.claim.slice(0, 1)) >= reach ? "paths cross" : "too close";
        let kind = "yield";
        const pair = [a.key, b.key].sort().join("|");
        seen.add(pair);
        // l's body stands in w's path.
        const blocks = (w, l) => gap(w.claim.slice(1), [[l.pose.x, l.pose.y]]) < reach;
        const aInWay = blocks(b, a), bInWay = blocks(a, b);
        if (aInWay !== bInWay) {
          [lo, hi] = aInWay ? [b, a] : [a, b];
          reason = `${hi.label} is in its way`;
        } else if (aInWay && bInWay && lanes.length) {
          // Head-on: the lower-ranked robot backs off along the lane.
          // Whichever of the two has the shorter way out backs off (ties: the lower-ranked).
          let plan = planRetreat(lanes, lo, hi, reach, bots, sites);
          const other = planRetreat(lanes, hi, lo, reach, bots, sites);
          if (other && (!plan || other.path.length < plan.path.length)) { [lo, hi] = [hi, lo]; plan = other; }
          if (plan) {
            const rt = { loser: lo.key, winner: hi.key, path: plan.path, into: plan.into || null,
              id: `${pair}@${now.toFixed(1)}`, from: [lo.pose.x, lo.pose.y] };
            mem.retreats.set(pair, rt);
            const where = plan.into
              ? `into ${plan.into.name} (${plan.into.state === "occupied" ? "under the rack" : "free slot"})`
              : plan.spur ? `${(plan.path.length * 0.1).toFixed(1)} m back up its spur`
                : `${(plan.path.length * 0.1).toFixed(1)} m along the lane`;
            events.push({ robot: lo.key, tone: "warn", text: `${lo.label} backs off ${where} to let ${hi.label} pass.` });
            wants.set(lo.key, { winner: hi, reason: `backing off for ${hi.label}`, retreat: rt });
            conflicts.push({ a: hi.key, b: lo.key, kind: "yield", reason: "backing off to let it pass", gap: +g.toFixed(2) });
            continue;
          }
        }
        let flip = mem.flipped.get(pair);
        if (!flip && blocks(hi, lo) && stuck(hi, hi.movingSince || 0)) {
          flip = { since: now, winner: lo.key };
          mem.flipped.set(pair, flip);
          events.push({ robot: lo.key, tone: "warn", text: `${hi.label} is blocked by ${lo.label}; ${lo.label} goes first to clear the way.` });
        }
        if (flip) {
          if (flip.winner === lo.key) [lo, hi] = [hi, lo];
          goNow.add(hi.key);
          reason = "clearing the way";
          if (now - flip.since > 2 * STUCK_AFTER && stuck(hi, flip.since) && blocks(hi, lo)) {
            kind = "deadlock";
            reason = "block each other";
            if (!flip.reported) {
              flip.reported = true;
              events.push({ robot: hi.key, tone: "fault", text: `${hi.label} and ${lo.label} block each other. Drive one aside from its console.` });
            }
          }
        }
        // A loop breaker keeps right of way over everyone for a few seconds.
        const breaking = (r) => (mem.breakers.get(r.key)?.until || 0) > now;
        if (breaking(lo) && !breaking(hi) && !stuck(lo, mem.breakers.get(lo.key).at)) { [lo, hi] = [hi, lo]; reason = `${hi.label} breaks a hold loop`; }
        wants.set(lo.key, { winner: hi, reason });
        conflicts.push({ a: hi.key, b: lo.key, kind, reason, gap: +g.toFixed(2) });
      } else {
        const [mover, parked] = da ? [a, b] : [b, a];
        // A parked robot never moves on its own: if the mover can't get past, say so.
        const stuckBehind = !mover.hold && stuck(mover, mover.movingSince || 0);
        conflicts.push({ a: mover.key, b: parked.key, kind: stuckBehind ? "blocked" : "pass",
          reason: stuckBehind ? "stuck behind a parked robot" : "passing a parked robot", gap: +g.toFixed(2) });
      }
    }
  }
  for (const key of mem.flipped.keys()) if (!seen.has(key)) mem.flipped.delete(key);
  // Hold loops: follow who-waits-for-whom; a closed chain lets one robot go.
  const byKey = new Map(bots.map((b) => [b.key, b]));
  const waitsOn = new Map([...wants].map(([k, w]) => [k, w.winner.key]));
  const visited = new Set();
  for (const start of waitsOn.keys()) {
    const chain = [], at = new Map();
    let k = start;
    while (k && waitsOn.has(k) && !at.has(k) && !visited.has(k)) { at.set(k, chain.length); chain.push(k); k = waitsOn.get(k); }
    chain.forEach((c) => visited.add(c));
    if (!k || !at.has(k)) continue;
    const loop = chain.slice(at.get(k));
    // Its way ahead must be clear of every robot's body (not only the one it
    // waited for), or it gets right of way it can't use; one that got it and
    // didn't move is passed over for a while.
    const cand = loop.filter((c) => !wants.get(c).retreat && (mem.failed.get(c) || 0) <= now);
    const pool = cand.filter((c) => {
      const r = byKey.get(c);
      return bots.every((o) => o === r || gap(r.claim.slice(1), [[o.pose.x, o.pose.y]]) >= radiusOf(r.mission) + radiusOf(o.mission) + CLEARANCE);
    });
    if (!pool.length) continue;
    const go = pool.reduce((a, b) => (outranks(byKey.get(b), byKey.get(a)) ? b : a));
    mem.breakers.set(go, { until: now + BREAK_FOR, at: now });
    wants.delete(go);
    goNow.add(go);
    events.push({ robot: go, tone: "warn", text: `Hold loop ${loop.map((c) => byKey.get(c).label).join(" → ")}: ${byKey.get(go).label} goes first.` });
  }
  for (const [k, b] of mem.breakers) {
    const r = byKey.get(k);
    if (r && now - b.at > STUCK_AFTER && (r.lastMoveAt || 0) < b.at + 0.5) { mem.failed.set(k, now + 20); mem.breakers.delete(k); }
    else if (b.until <= now) mem.breakers.delete(k);
  }
  for (const b of bots) {
    const w = wants.get(b.key);
    if (w) {
      if (!b.hold || b.hold.by !== w.winner.key) {
        if (!w.retreat) events.push({ robot: b.key, tone: "warn", text: `${b.label} holds for ${w.winner.label}: ${w.reason}.` });
        b.hold = { by: w.winner.key, by_label: w.winner.label, reason: w.reason, since: now };
      }
      b.hold.retreat = w.retreat ? { id: w.retreat.id, path: w.retreat.path, into: w.retreat.into } : null;
      b.hold.reason = w.reason;
      b.clearSince = null;
    } else if (b.hold) {
      b.clearSince = b.clearSince || now;
      if (now - b.clearSince >= RELEASE_AFTER || goNow.has(b.key)) {
        events.push({ robot: b.key, tone: "info", text: `${b.label} is clear and continues.` });
        b.hold = null;
        b.clearSince = null;
      }
    }
  }
  return { conflicts, events };
}
