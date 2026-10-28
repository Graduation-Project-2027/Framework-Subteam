/**
 * Lane geometry shared by the map, the lane drawing tool and the Preview
 * robot. Mirrors mission_manager_node.py: points closer than MERGE_DIST are
 * one graph node, and every corner of a route (bends and junctions between
 * lanes) is rounded with the same radius rule as smooth_polyline().
 */
export const MERGE_DIST = 0.5;
export const DEFAULT_RADIUS = 0.6;

const hyp = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

/** Circular fillet at b for the corner a→b→c, or null when straight. */
export function cornerArc(a, b, c, radius, step = 0.05) {
  const l1 = hyp(a, b), l2 = hyp(b, c);
  if (l1 < 1e-3 || l2 < 1e-3 || radius <= 0) return null;
  const ux = (b.x - a.x) / l1, uy = (b.y - a.y) / l1;
  const vx = (c.x - b.x) / l2, vy = (c.y - b.y) / l2;
  const turn = Math.atan2(ux * vy - uy * vx, ux * vx + uy * vy);
  if (Math.abs(turn) < 1e-3) return null;
  const half = Math.tan(Math.abs(turn) / 2);
  const d = Math.min(radius * half, 0.5 * l1, 0.5 * l2);
  const r = d / half;
  const side = turn > 0 ? 1 : -1;
  const sx = b.x - ux * d, sy = b.y - uy * d;
  const ox = sx - uy * r * side, oy = sy + ux * r * side;
  const a0 = Math.atan2(sy - oy, sx - ox);
  const n = Math.max(2, Math.ceil((Math.abs(turn) * r) / step));
  const points = [];
  for (let k = 0; k <= n; k++) {
    const t = a0 + (side * Math.abs(turn) * k) / n;
    points.push({ x: ox + r * Math.cos(t), y: oy + r * Math.sin(t) });
  }
  return { points, d, turn };
}

/** Round every interior corner and resample every `step` metres. */
export function smoothPolyline(input, radius = DEFAULT_RADIUS, step = 0.05) {
  const pts = [];
  for (const p of input) if (!pts.length || hyp(pts[pts.length - 1], p) > 1e-3) pts.push({ x: p.x, y: p.y });
  if (pts.length < 2) return pts;
  const out = [pts[0]];
  for (let i = 1; i < pts.length - 1; i++) {
    const arc = cornerArc(pts[i - 1], pts[i], pts[i + 1], radius, step);
    if (arc) out.push(...arc.points);
    else out.push(pts[i]);
  }
  out.push(pts[pts.length - 1]);
  const res = [out[0]];
  let carry = 0;
  for (let i = 1; i < out.length; i++) {
    const a = out[i - 1], b = out[i];
    const seg = hyp(a, b);
    let t = step - carry;
    while (t < seg) {
      res.push({ x: a.x + ((b.x - a.x) * t) / seg, y: a.y + ((b.y - a.y) * t) / seg });
      t += step;
    }
    carry = seg - (t - step);
  }
  if (hyp(res[res.length - 1], out[out.length - 1]) > 1e-3) res.push(out[out.length - 1]);
  return res;
}

/** Lane graph exactly as the mission manager builds it. */
export function buildGraph(lanes) {
  const nodes = [];
  const adj = [];
  const find = (x, y) => {
    for (let i = 0; i < nodes.length; i++) {
      if ((nodes[i].x - x) ** 2 + (nodes[i].y - y) ** 2 < MERGE_DIST * MERGE_DIST) return i;
    }
    nodes.push({ x, y });
    adj.push([]);
    return nodes.length - 1;
  };
  for (const lane of lanes || []) {
    let prev = null;
    for (const p of lane.points || []) {
      const i = find(Number(p.x), Number(p.y));
      if (prev !== null && prev !== i && !adj[i].some((e) => e.j === prev)) {
        const d = hyp(nodes[i], nodes[prev]);
        adj[i].push({ j: prev, d });
        adj[prev].push({ j: i, d });
      }
      prev = i;
    }
  }
  return { nodes, adj };
}

/**
 * Drawable lane network: straight edge pieces between corner tangents and
 * a rounded arc for every turn the robot can take at a node. An edge end is
 * only kept sharp where it continues straight through the node.
 */
export function laneGeometry(lanes, radius = DEFAULT_RADIUS) {
  const { nodes, adj } = buildGraph(lanes);
  const arcs = [];
  const trim = adj.map((list) => list.map(() => 0));
  nodes.forEach((n, i) => {
    const inc = adj[i];
    const straight = new Set();
    for (let a = 0; a < inc.length; a++) {
      for (let b = a + 1; b < inc.length; b++) {
        const A = nodes[inc[a].j], C = nodes[inc[b].j];
        const arc = cornerArc(A, n, C, radius);
        if (!arc) { straight.add(a); straight.add(b); continue; }
        if (Math.abs(arc.turn) < 0.17) { straight.add(a); straight.add(b); continue; }
        arcs.push(arc.points);
        trim[i][a] = Math.max(trim[i][a], arc.d);
        trim[i][b] = Math.max(trim[i][b], arc.d);
      }
    }
    straight.forEach((k) => { trim[i][k] = 0; });
  });
  const edges = [];
  nodes.forEach((n, i) => {
    adj[i].forEach((e, k) => {
      if (e.j < i) return;
      const back = adj[e.j].findIndex((x) => x.j === i);
      const m = nodes[e.j];
      const ux = (m.x - n.x) / e.d, uy = (m.y - n.y) / e.d;
      const t0 = trim[i][k], t1 = back >= 0 ? trim[e.j][back] : 0;
      if (t0 + t1 >= e.d - 1e-3) return;
      edges.push([{ x: n.x + ux * t0, y: n.y + uy * t0 }, { x: m.x - ux * t1, y: m.y - uy * t1 }]);
    });
  });
  return { nodes, adj, edges, arcs };
}

function project(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const L = dx * dx + dy * dy;
  if (L < 1e-9) return { x: a.x, y: a.y, t: 0, d: hyp(p, a) };
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / L));
  const q = { x: a.x + t * dx, y: a.y + t * dy };
  return { ...q, t, d: hyp(p, q) };
}

function segIntersect(p, p2, q, q2) {
  const r = { x: p2.x - p.x, y: p2.y - p.y }, s = { x: q2.x - q.x, y: q2.y - q.y };
  const den = r.x * s.y - r.y * s.x;
  if (Math.abs(den) < 1e-9) return null;
  const t = ((q.x - p.x) * s.y - (q.y - p.y) * s.x) / den;
  const u = ((q.x - p.x) * r.y - (q.y - p.y) * r.x) / den;
  if (t <= 0 || t >= 1 || u <= 0 || u >= 1) return null;
  return { x: p.x + t * r.x, y: p.y + t * r.y, t, u };
}

/**
 * Connect a newly drawn lane to the network: ends near a lane snap onto it
 * (a junction point is inserted into that lane), and crossings get a shared
 * junction point in both lanes. Returns the new lane points and the updated
 * existing lanes.
 */
export function connectLane(points, lanes) {
  let pts = points.map((p) => ({ x: p.x, y: p.y }));
  let others = (lanes || []).map((l) => ({ ...l, points: l.points.map((p) => ({ x: p.x, y: p.y })) }));
  const nodesOf = () => others.flatMap((l) => l.points);

  const snapEnd = (idx) => {
    const p = pts[idx];
    const node = nodesOf().find((q) => hyp(p, q) < MERGE_DIST);
    if (node) { pts[idx] = { ...node }; return; }
    let best = null;
    others.forEach((l, li) => {
      for (let s = 0; s < l.points.length - 1; s++) {
        const f = project(p, l.points[s], l.points[s + 1]);
        if (f.d < MERGE_DIST && (!best || f.d < best.f.d)) best = { li, s, f };
      }
    });
    if (best) {
      const foot = { x: best.f.x, y: best.f.y };
      others[best.li].points.splice(best.s + 1, 0, foot);
      pts[idx] = { ...foot };
    }
  };
  snapEnd(0);
  if (pts.length > 1) snapEnd(pts.length - 1);

  const insertOnLane = (lane, p) => {
    for (let k = 0; k < lane.points.length - 1; k++) {
      if (project(p, lane.points[k], lane.points[k + 1]).d < 1e-6) {
        lane.points.splice(k + 1, 0, { x: p.x, y: p.y });
        return;
      }
    }
  };

  const nextPts = [pts[0]];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    const hits = [];
    others.forEach((l, li) => {
      for (let s = 0; s < l.points.length - 1; s++) {
        const x = segIntersect(a, b, l.points[s], l.points[s + 1]);
        if (x && hyp(x, a) > MERGE_DIST && hyp(x, b) > MERGE_DIST) hits.push({ ...x, li });
      }
    });
    hits.sort((h1, h2) => h1.t - h2.t);
    for (const h of hits) {
      insertOnLane(others[h.li], h);
      nextPts.push({ x: h.x, y: h.y });
    }
    nextPts.push(b);
  }
  pts = nextPts;
  return { points: pts, lanes: others };
}

/**
 * Route like _route_via_lanes(): nearest lane node → graph → the foot of the
 * target on the approach side. Returns waypoints or null when there are no
 * usable lanes.
 */
export function laneRoute(lanes, from, target) {
  const { nodes, adj } = buildGraph(lanes);
  if (!nodes.length) return null;
  const nodeAt = (p) => nodes.findIndex((n) => hyp(n, p) < MERGE_DIST);
  const ax = -Math.cos(target.yaw || 0), ay = -Math.sin(target.yaw || 0);
  let best = null;
  for (const lane of lanes) {
    for (let s = 0; s < lane.points.length - 1; s++) {
      const A = lane.points[s], B = lane.points[s + 1];
      const f = project(target, A, B);
      const dot = (f.x - target.x) * ax + (f.y - target.y) * ay;
      const score = dot >= 0 ? f.d : f.d + 1000;
      const a = nodeAt(A), b = nodeAt(B);
      if (a < 0 || b < 0) continue;
      if (!best || score < best.score) best = { score, foot: { x: f.x, y: f.y }, a, b };
    }
  }
  if (!best) return null;
  let start = 0, sd = Infinity;
  nodes.forEach((n, i) => { const d = hyp(n, from); if (d < sd) { sd = d; start = i; } });
  const dist = new Array(nodes.length).fill(Infinity);
  const prev = new Array(nodes.length).fill(-1);
  const done = new Array(nodes.length).fill(false);
  dist[start] = 0;
  for (;;) {
    let u = -1;
    for (let i = 0; i < nodes.length; i++) if (!done[i] && (u < 0 || dist[i] < dist[u])) u = i;
    if (u < 0 || dist[u] === Infinity) break;
    done[u] = true;
    for (const e of adj[u]) if (dist[u] + e.d < dist[e.j]) { dist[e.j] = dist[u] + e.d; prev[e.j] = u; }
  }
  const cost = (i) => dist[i] + hyp(nodes[i], best.foot);
  const end = cost(best.a) <= cost(best.b) ? best.a : best.b;
  if (dist[end] === Infinity) return null;
  const path = [];
  for (let i = end; i !== -1; i = prev[i]) path.push(nodes[i]);
  path.reverse();
  if (hyp(path[path.length - 1], best.foot) > 0.05) path.push(best.foot);
  return path;
}
