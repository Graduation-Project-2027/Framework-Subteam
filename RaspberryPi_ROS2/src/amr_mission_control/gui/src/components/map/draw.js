/* Canvas drawing for the floor plan, in screen space.
   `V` is the camera: V.s is screen px per metre, V.sx(x)/V.sy(y) map world
   metres to CSS pixels (y up in the world, down on screen). */
import { RACK_SIDE, ROBOT_LEN, ROBOT_WID } from "../../lib/geometry";

export const C = {
  ink: "#141416",
  paper: "#ffffff",
  unknown: "#e8e9ec",
  wall: "#26272b",
  red: "#e3202a",
  gold: "#de9242",
  goldInk: "#8a5410",
  grid: "rgba(20,20,22,0.05)",
  gridMajor: "rgba(20,20,22,0.10)",
  font: '"Archivo Variable", Archivo, system-ui, sans-serif',
};

/* ── rasters ──────────────────────────────────────────────────────── */
const rasterCache = new WeakMap();

function costColor(v) {
  if (v === 100) return [227, 32, 42, 150];
  if (v === 99) return [227, 32, 42, 82];
  if (v > 0) return [222, 146, 66, Math.round(24 + (v / 98) * 96)];
  return null;
}

/** Offscreen canvas for a grid; top image row is the grid's highest y. */
export function rasterize(g, palette) {
  const key = rasterCache.get(g);
  if (key && key.version === g.version && key.palette === palette) return key.canvas;
  const canvas = key?.canvas && key.canvas.width === g.width && key.canvas.height === g.height
    ? key.canvas : document.createElement("canvas");
  canvas.width = g.width;
  canvas.height = g.height;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(g.width, g.height);
  const px = img.data;
  for (let row = 0; row < g.height; row++) {
    const gy = g.height - 1 - row;
    for (let x = 0; x < g.width; x++) {
      const v = g.data[gy * g.width + x];
      const o = (row * g.width + x) * 4;
      if (palette === "map") {
        const c = v < 0 ? 232 : v >= 65 ? 38 : 255;
        px[o] = c; px[o + 1] = v < 0 ? 233 : v >= 65 ? 39 : 255; px[o + 2] = v < 0 ? 236 : v >= 65 ? 43 : 255; px[o + 3] = 255;
      } else {
        const c = costColor(v);
        if (c) { px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = c[3]; }
      }
    }
  }
  ctx.putImageData(img, 0, 0);
  rasterCache.set(g, { canvas, version: g.version, palette });
  return canvas;
}

export function drawRaster(ctx, g, palette, V, alpha = 1) {
  const canvas = rasterize(g, palette);
  const w = g.width * g.resolution * V.s, h = g.height * g.resolution * V.s;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.imageSmoothingEnabled = V.s * g.resolution < 1.5;
  if (g.toMap) {
    const t = g.toMap;
    ctx.translate(V.sx(t.x), V.sy(t.y));
    ctx.rotate(-t.yaw);
    ctx.drawImage(canvas, g.origin.x * V.s, -(g.origin.y + g.height * g.resolution) * V.s, w, h);
  } else {
    ctx.drawImage(canvas, V.sx(g.origin.x), V.sy(g.origin.y + g.height * g.resolution), w, h);
  }
  ctx.restore();
}

/** 1 m grid (5 m major), aligned to the world origin, within `b`. */
export function drawGrid(ctx, V, b) {
  const first = (v) => Math.ceil(v);
  ctx.save();
  ctx.lineWidth = 1;
  const minor = V.s >= 12;
  for (let x = first(b.x0); x <= b.x1; x++) {
    const major = x % 5 === 0;
    if (!major && !minor) continue;
    ctx.strokeStyle = major ? C.gridMajor : C.grid;
    const X = Math.round(V.sx(x)) + 0.5;
    ctx.beginPath(); ctx.moveTo(X, V.sy(b.y0)); ctx.lineTo(X, V.sy(b.y1)); ctx.stroke();
  }
  for (let y = first(b.y0); y <= b.y1; y++) {
    const major = y % 5 === 0;
    if (!major && !minor) continue;
    ctx.strokeStyle = major ? C.gridMajor : C.grid;
    const Y = Math.round(V.sy(y)) + 0.5;
    ctx.beginPath(); ctx.moveTo(V.sx(b.x0), Y); ctx.lineTo(V.sx(b.x1), Y); ctx.stroke();
  }
  ctx.restore();
}

function line(ctx, V, pts) {
  ctx.beginPath();
  pts.forEach((p, i) => (i ? ctx.lineTo(V.sx(p.x), V.sy(p.y)) : ctx.moveTo(V.sx(p.x), V.sy(p.y))));
}

/* ── lanes ────────────────────────────────────────────────────────── */
/** Lane network as floor tape: straight pieces + rounded turns, no sharp corner. */
export function drawLaneNetwork(ctx, V, geo, { highlight = null } = {}) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = C.gold;
  ctx.lineWidth = Math.max(3, 0.1 * V.s);
  for (const [a, b] of geo.edges) { line(ctx, V, [a, b]); ctx.stroke(); }
  for (const arc of geo.arcs) { line(ctx, V, arc); ctx.stroke(); }
  if (highlight?.length > 1) {
    ctx.strokeStyle = C.goldInk;
    ctx.lineWidth = Math.max(4, 0.12 * V.s);
    line(ctx, V, highlight);
    ctx.stroke();
  }
  // junctions where lanes meet
  ctx.fillStyle = C.paper;
  ctx.strokeStyle = C.goldInk;
  ctx.lineWidth = 1.5;
  geo.nodes.forEach((n, i) => {
    if (geo.adj[i].length < 3) return;
    ctx.beginPath(); ctx.arc(V.sx(n.x), V.sy(n.y), 3.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  });
  ctx.restore();
}

export function drawLaneDraft(ctx, V, points, smooth, cursor) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = C.gold;
  ctx.lineWidth = Math.max(3, 0.1 * V.s);
  if (smooth.length > 1) { line(ctx, V, smooth); ctx.stroke(); }
  if (cursor && points.length) {
    ctx.globalAlpha = 0.5;
    ctx.setLineDash([6, 5]);
    line(ctx, V, [points[points.length - 1], cursor]);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.globalAlpha = 1;
  }
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = C.goldInk;
  ctx.fillStyle = C.paper;
  points.forEach((p) => { ctx.beginPath(); ctx.arc(V.sx(p.x), V.sy(p.y), 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); });
  ctx.restore();
}

/* ── robot world ──────────────────────────────────────────────────── */
/** Nav2 global plan: the robot's intention, in the robot's red. */
export function drawPlan(ctx, V, plan) {
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = C.red;
  ctx.lineWidth = 3;
  ctx.setLineDash([1, 6]);
  line(ctx, V, plan);
  ctx.stroke();
  ctx.setLineDash([]);
  const g = plan[plan.length - 1];
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(V.sx(g.x), V.sy(g.y), 6, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = C.red;
  ctx.beginPath(); ctx.arc(V.sx(g.x), V.sy(g.y), 2, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function rect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function tag(ctx, x, y, text, tone) {
  ctx.save();
  ctx.font = `600 11px ${C.font}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 8;
  ctx.fillStyle = tone === "ink" ? C.ink : C.paper;
  rect(ctx, x - w / 2, y - 15, w, 15, 3);
  ctx.fill();
  if (tone !== "ink") { ctx.strokeStyle = tone === "gold" ? C.gold : C.ink; ctx.lineWidth = 1; ctx.stroke(); }
  ctx.fillStyle = tone === "ink" ? C.paper : tone === "gold" ? C.goldInk : C.ink;
  ctx.fillText(text, x, y - 7);
  ctx.restore();
}

function outline(ctx, half) {
  ctx.strokeStyle = C.ink;
  ctx.lineWidth = 2;
  ctx.setLineDash([]);
  rect(ctx, -half - 3, -half - 3, (half + 3) * 2, (half + 3) * 2, 3);
  ctx.stroke();
}

export function drawDock(ctx, V, dock, hovered) {
  const x = V.sx(dock.x), y = V.sy(dock.y);
  const half = Math.max(7, 0.45 * V.s);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-(dock.yaw || 0));
  ctx.fillStyle = "rgba(222,146,66,0.16)";
  ctx.strokeStyle = C.gold;
  ctx.lineWidth = Math.max(2, 0.06 * V.s);
  rect(ctx, -half, -half, half * 2, half * 2, 2);
  ctx.fill(); ctx.stroke();
  ctx.fillStyle = C.goldInk;
  ctx.beginPath();
  ctx.moveTo(half * 0.55, 0);
  ctx.lineTo(-half * 0.25, -half * 0.4);
  ctx.lineTo(-half * 0.25, half * 0.4);
  ctx.closePath(); ctx.fill();
  if (hovered) outline(ctx, half);
  ctx.restore();
  tag(ctx, x, y - half - 6, dock.name || "Dock", "gold");
}

/** Racks & drop zones. Occupied = solid ink, free = hollow. */
export function drawSite(ctx, V, site, kind, hovered, faint = false) {
  const x = V.sx(site.x), y = V.sy(site.y);
  const occupied = (site.state || (kind === "rack" ? "occupied" : "free")) === "occupied";
  const half = (RACK_SIDE / 2) * V.s;
  ctx.save();
  if (faint) ctx.globalAlpha = 0.35;
  ctx.translate(x, y);
  ctx.rotate(-(site.yaw || 0));
  ctx.lineWidth = 1.5;
  ctx.strokeStyle = C.ink;
  ctx.fillStyle = occupied ? C.ink : C.paper;
  if (kind === "zone" && !occupied) ctx.setLineDash([4, 3]);
  rect(ctx, -half, -half, half * 2, half * 2, 1.5);
  ctx.fill(); ctx.stroke();
  ctx.setLineDash([]);
  if (kind === "rack") {
    const leg = Math.max(2.2, 0.07 * V.s);
    ctx.fillStyle = occupied ? C.paper : C.ink;
    [[-1, -1], [1, -1], [1, 1], [-1, 1]].forEach(([sx, sy]) => {
      ctx.fillRect(sx * (half - leg / 2) - leg / 2, sy * (half - leg / 2) - leg / 2, leg, leg);
    });
  }
  ctx.strokeStyle = occupied ? C.paper : C.ink;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(half * 0.72, 0); ctx.lineTo(half * 0.98, 0); ctx.stroke();
  if (hovered) outline(ctx, half);
  ctx.restore();

  const name = site.name || site.id;
  if (half * 2 >= 26) {
    ctx.save();
    if (faint) ctx.globalAlpha = 0.35;
    ctx.font = `600 ${Math.min(13, half * 0.72)}px ${C.font}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = occupied ? C.paper : C.ink;
    ctx.fillText(name, x, y + 0.5);
    ctx.restore();
  } else if (half * 2 >= 10 && !faint) {
    tag(ctx, x, y - half - 4, name, occupied ? "ink" : "paper");
  }
}

export function drawPoints(ctx, V, pts, color, size) {
  if (!pts?.length) return;
  ctx.save();
  ctx.fillStyle = color;
  const h = size / 2;
  for (let i = 0; i < pts.length; i += 2) ctx.fillRect(V.sx(pts[i]) - h, V.sy(pts[i + 1]) - h, size, size);
  ctx.restore();
}

/** Lidar hits the map can't explain: boxed and labelled. */
export function drawDynamic(ctx, V, boxes) {
  ctx.save();
  ctx.strokeStyle = C.red;
  ctx.fillStyle = "rgba(227,32,42,0.10)";
  ctx.lineWidth = 2;
  for (const b of boxes) {
    const pad = 0.08;
    const x = V.sx(b.x0 - pad), y = V.sy(b.y1 + pad);
    const w = (b.x1 - b.x0 + 2 * pad) * V.s, h = (b.y1 - b.y0 + 2 * pad) * V.s;
    rect(ctx, x, y, Math.max(w, 8), Math.max(h, 8), 3);
    ctx.fill(); ctx.stroke();
  }
  ctx.restore();
}

export function drawFootprint(ctx, V, poly) {
  if (!poly?.length) return;
  ctx.save();
  ctx.strokeStyle = C.ink;
  ctx.lineWidth = 1.25;
  ctx.setLineDash([4, 3]);
  line(ctx, V, [...poly, poly[0]]);
  ctx.stroke();
  ctx.restore();
}

export function drawLocalWindow(ctx, V, g) {
  if (!g) return;
  ctx.save();
  ctx.strokeStyle = "rgba(20,20,22,0.25)";
  ctx.setLineDash([2, 4]);
  ctx.lineWidth = 1;
  const corners = [[0, 0], [g.width, 0], [g.width, g.height], [0, g.height]].map(([cx, cy]) => {
    const lx = g.origin.x + cx * g.resolution, ly = g.origin.y + cy * g.resolution;
    if (!g.toMap) return { x: lx, y: ly };
    const t = g.toMap, c = Math.cos(t.yaw), s = Math.sin(t.yaw);
    return { x: t.x + c * lx - s * ly, y: t.y + s * lx + c * ly };
  });
  line(ctx, V, [...corners, corners[0]]);
  ctx.stroke();
  ctx.restore();
}

export function drawTrail(ctx, V, trail) {
  if (trail.length < 2) return;
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineWidth = 2;
  for (let i = 1; i < trail.length; i++) {
    ctx.strokeStyle = `rgba(20,20,22,${(i / trail.length) * 0.35})`;
    line(ctx, V, [trail[i - 1], trail[i]]);
    ctx.stroke();
  }
  ctx.restore();
}

/** The robot is the emblem: black chassis, E-JUST red sun at its centre. */
export function drawRobot(ctx, V, pose, { carrying, live, t, reduceMotion }) {
  const x = V.sx(pose.x), y = V.sy(pose.y);
  const k = Math.max(1, 9 / ((ROBOT_WID / 2) * V.s)); // stay findable when zoomed out
  const hl = (ROBOT_LEN / 2) * V.s * k, hw = (ROBOT_WID / 2) * V.s * k;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(-(pose.yaw || 0));
  if (live && !reduceMotion) {
    const phase = (t % 1600) / 1600;
    ctx.beginPath();
    ctx.arc(0, 0, hw * (1.1 + phase * 1.6), 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(227,32,42,${0.45 * (1 - phase)})`;
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  if (carrying) {
    const rh = (RACK_SIDE / 2) * V.s * k;
    ctx.setLineDash([4, 3]);
    ctx.strokeStyle = C.ink;
    ctx.lineWidth = 1.5;
    rect(ctx, -rh, -rh, rh * 2, rh * 2, 2);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.shadowColor = "rgba(20,20,22,0.25)";
  ctx.shadowBlur = 6;
  ctx.shadowOffsetY = 2;
  ctx.fillStyle = C.ink;
  rect(ctx, -hl, -hw, hl * 2, hw * 2, Math.min(hl, hw) * 0.28);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.fillStyle = C.red;
  ctx.beginPath(); ctx.arc(-hl * 0.08, 0, hw * 0.52, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.paper;
  ctx.beginPath();
  ctx.moveTo(hl * 0.93, 0);
  ctx.lineTo(hl * 0.62, -hw * 0.3);
  ctx.lineTo(hl * 0.62, hw * 0.3);
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

/** Screen-space arrow while picking a pose. */
export function drawPickArrow(ctx, a) {
  ctx.save();
  ctx.strokeStyle = C.ink;
  ctx.fillStyle = C.ink;
  ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(a.sx, a.sy, 5, 0, Math.PI * 2); ctx.fill();
  const len = Math.hypot(a.ex - a.sx, a.ey - a.sy);
  if (len > 8) {
    ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(a.ex, a.ey); ctx.stroke();
    const ang = Math.atan2(a.ey - a.sy, a.ex - a.sx);
    ctx.beginPath();
    ctx.moveTo(a.ex, a.ey);
    ctx.lineTo(a.ex - 11 * Math.cos(ang - 0.42), a.ey - 11 * Math.sin(ang - 0.42));
    ctx.lineTo(a.ex - 11 * Math.cos(ang + 0.42), a.ey - 11 * Math.sin(ang + 0.42));
    ctx.closePath(); ctx.fill();
    const label = `${Math.round((a.yaw * 180) / Math.PI)}°`;
    ctx.font = `600 12px ${C.font}`;
    const w = ctx.measureText(label).width + 10;
    rect(ctx, a.ex + 10, a.ey - 22, w, 18, 4); ctx.fill();
    ctx.fillStyle = C.paper;
    ctx.fillText(label, a.ex + 15, a.ey - 9);
  }
  ctx.restore();
}

/** Brush outline while editing a map. */
export function drawBrush(ctx, V, p, radius, value) {
  ctx.save();
  ctx.strokeStyle = value === "wall" ? C.ink : value === "free" ? C.red : C.goldInk;
  ctx.lineWidth = 1.5;
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.arc(V.sx(p.x), V.sy(p.y), Math.max(2, radius * V.s), 0, Math.PI * 2); ctx.stroke();
  ctx.restore();
}
