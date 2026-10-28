/**
 * Occupancy grids in ROS convention: row 0 is the lowest y, cells are
 * -1 unknown or 0..100. Used for the live SLAM map, saved maps (PGM),
 * costmaps and the Preview world alike.
 */

export const BUNDLED_MAP = {
  url: "/map.png",
  resolution: 0.05,
  origin: [-22.024, -9.478],
  negate: 0,
  occupied_thresh: 0.65,
  free_thresh: 0.196,
};

let versionSeq = 1;
export const nextVersion = () => versionSeq++;

export function makeGrid({ width, height, resolution, origin, data, frame = "map" }) {
  return {
    width,
    height,
    resolution,
    origin: { x: origin.x ?? origin[0], y: origin.y ?? origin[1] },
    data: data || new Int8Array(width * height).fill(-1),
    frame,
    version: nextVersion(),
  };
}

export function cloneGrid(g) {
  return { ...g, origin: { ...g.origin }, data: new Int8Array(g.data), version: nextVersion() };
}

export function touch(g) {
  g.version = nextVersion();
  return g;
}

function toInt8(data) {
  if (data instanceof Int8Array) return data;
  if (typeof data === "string") {
    const bin = atob(data);
    const out = new Int8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = (bin.charCodeAt(i) << 24) >> 24;
    return out;
  }
  if (ArrayBuffer.isView(data)) return new Int8Array(data.buffer, data.byteOffset, data.byteLength);
  return Int8Array.from(data || []);
}

export function toBytes(data) {
  if (data instanceof Uint8Array) return data;
  if (typeof data === "string") {
    const bin = atob(data);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  return Uint8Array.from(data || []);
}

export function toFloats(data) {
  if (data instanceof Float32Array || data instanceof Float64Array) return data;
  if (ArrayBuffer.isView(data)) return new Float32Array(data.buffer, data.byteOffset, data.byteLength / 4);
  return Float32Array.from(data || []);
}

/** nav_msgs/OccupancyGrid (JSON or CBOR) → grid. */
export function gridFromMsg(msg) {
  const info = msg.info;
  const p = info.origin.position;
  return makeGrid({
    width: info.width,
    height: info.height,
    resolution: info.resolution,
    origin: { x: p.x, y: p.y },
    data: toInt8(msg.data),
    frame: msg.header?.frame_id || "map",
  });
}

/** Binary PGM (P5) + map yaml fields → grid, using the map_server trinary rules. */
export function gridFromPGM(buffer, meta) {
  const bytes = new Uint8Array(buffer);
  if (bytes[0] !== 0x50 || bytes[1] !== 0x35) throw new Error("Not a binary PGM image");
  const fields = [];
  let i = 2;
  while (fields.length < 3) {
    while (i < bytes.length && /\s/.test(String.fromCharCode(bytes[i]))) i++;
    if (bytes[i] === 0x23) { while (i < bytes.length && bytes[i] !== 0x0a) i++; continue; }
    let s = "";
    while (i < bytes.length && !/\s/.test(String.fromCharCode(bytes[i]))) s += String.fromCharCode(bytes[i++]);
    fields.push(parseInt(s, 10));
  }
  i += 1;
  const [w, h, maxval] = fields;
  const occ = meta.occupied_thresh ?? 0.65;
  const free = meta.free_thresh ?? 0.196;
  const data = new Int8Array(w * h);
  for (let row = 0; row < h; row++) {
    const gy = h - 1 - row;
    for (let x = 0; x < w; x++) {
      const v = bytes[i + row * w + x] / maxval;
      const p = meta.negate ? v : 1 - v;
      data[gy * w + x] = p > occ ? 100 : p < free ? 0 : -1;
    }
  }
  return makeGrid({ width: w, height: h, resolution: meta.resolution, origin: meta.origin, data });
}

/** grid → binary PGM (P5) with map_saver's trinary values. */
export function gridToPGM(g) {
  const header = new TextEncoder().encode(`P5\n# LEVERAGE map editor\n${g.width} ${g.height}\n255\n`);
  const out = new Uint8Array(header.length + g.width * g.height);
  out.set(header);
  let o = header.length;
  for (let row = 0; row < g.height; row++) {
    const gy = g.height - 1 - row;
    for (let x = 0; x < g.width; x++) {
      const v = g.data[gy * g.width + x];
      out[o++] = v < 0 ? 205 : v >= 50 ? 0 : 254;
    }
  }
  return out;
}

/** The bundled map.png (Hang1), used by Preview and when no launcher is reachable. */
let bundled = null;
export function loadBundledMap() {
  if (bundled) return bundled;
  bundled = new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0);
      const px = ctx.getImageData(0, 0, c.width, c.height).data;
      const w = c.width, h = c.height;
      const data = new Int8Array(w * h);
      for (let row = 0; row < h; row++) {
        for (let x = 0; x < w; x++) {
          const v = px[(row * w + x) * 4];
          data[(h - 1 - row) * w + x] = v >= 250 ? 0 : v <= 60 ? 100 : -1;
        }
      }
      resolve(makeGrid({ width: w, height: h, resolution: BUNDLED_MAP.resolution, origin: BUNDLED_MAP.origin, data }));
    };
    img.onerror = () => reject(new Error("map.png could not be loaded"));
    img.src = BUNDLED_MAP.url;
  });
  return bundled;
}

/* ── geometry ─────────────────────────────────────────────────────── */
export function cellIndex(g, x, y) {
  const cx = Math.floor((x - g.origin.x) / g.resolution);
  const cy = Math.floor((y - g.origin.y) / g.resolution);
  if (cx < 0 || cy < 0 || cx >= g.width || cy >= g.height) return -1;
  return cy * g.width + cx;
}

export function valueAt(g, x, y) {
  const i = cellIndex(g, x, y);
  return i < 0 ? -1 : g.data[i];
}

export function gridBounds(g) {
  return {
    x0: g.origin.x, y0: g.origin.y,
    x1: g.origin.x + g.width * g.resolution, y1: g.origin.y + g.height * g.resolution,
  };
}

/** Bounding box (world) of the known cells, for fitting the view. */
export function knownBounds(g) {
  let cx0 = g.width, cy0 = g.height, cx1 = -1, cy1 = -1;
  for (let y = 0; y < g.height; y++) {
    const row = y * g.width;
    for (let x = 0; x < g.width; x++) {
      if (g.data[row + x] !== -1) {
        if (x < cx0) cx0 = x;
        if (x > cx1) cx1 = x;
        if (y < cy0) cy0 = y;
        if (y > cy1) cy1 = y;
      }
    }
  }
  if (cx1 < 0) return null;
  const r = g.resolution;
  return { x0: g.origin.x + cx0 * r, y0: g.origin.y + cy0 * r, x1: g.origin.x + (cx1 + 1) * r, y1: g.origin.y + (cy1 + 1) * r };
}

/** Distance (m) along a ray until an occupied cell, or Infinity. */
export function raycast(g, x, y, ang, maxRange) {
  const step = g.resolution * 0.5;
  const dx = Math.cos(ang) * step, dy = Math.sin(ang) * step;
  let px = x, py = y;
  for (let d = 0; d < maxRange; d += step) {
    if (valueAt(g, px, py) >= 65) return d;
    px += dx;
    py += dy;
  }
  return Infinity;
}

/**
 * Inflate occupied cells into a Nav2-style costmap (0..100, 99 inscribed,
 * 100 lethal), using a two-pass chamfer distance transform.
 */
export function inflate(g, { inscribed = 0.32, radius = 0.55, scaling = 1.5, unknown = true } = {}) {
  const { width: w, height: h, resolution: r } = g;
  const INF = 1e9;
  const dist = new Float32Array(w * h).fill(INF);
  for (let i = 0; i < g.data.length; i++) if (g.data[i] >= 65) dist[i] = 0;
  const D1 = 1, D2 = Math.SQRT2;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      let d = dist[i];
      if (x > 0) d = Math.min(d, dist[i - 1] + D1);
      if (y > 0) {
        d = Math.min(d, dist[i - w] + D1);
        if (x > 0) d = Math.min(d, dist[i - w - 1] + D2);
        if (x < w - 1) d = Math.min(d, dist[i - w + 1] + D2);
      }
      dist[i] = d;
    }
  }
  for (let y = h - 1; y >= 0; y--) {
    for (let x = w - 1; x >= 0; x--) {
      const i = y * w + x;
      let d = dist[i];
      if (x < w - 1) d = Math.min(d, dist[i + 1] + D1);
      if (y < h - 1) {
        d = Math.min(d, dist[i + w] + D1);
        if (x < w - 1) d = Math.min(d, dist[i + w + 1] + D2);
        if (x > 0) d = Math.min(d, dist[i + w - 1] + D2);
      }
      dist[i] = d;
    }
  }
  const out = new Int8Array(w * h);
  for (let i = 0; i < out.length; i++) {
    const d = dist[i] * r;
    let c;
    if (dist[i] === 0) c = 100;
    else if (d <= inscribed) c = 99;
    else if (d <= radius) c = Math.max(1, Math.round(98 * Math.exp(-scaling * (d - inscribed))));
    else c = 0;
    if (unknown && g.data[i] === -1 && c < 99) c = -1;
    out[i] = c;
  }
  return makeGrid({ width: w, height: h, resolution: r, origin: g.origin, data: out, frame: g.frame });
}

/* ── A* (Preview planner) ─────────────────────────────────────────── */
const PLAN_CELL = 4; // grid cells per planner cell

function plannerGrid(g) {
  if (g._planner && g._planner.version === g.version) return g._planner;
  const gw = Math.ceil(g.width / PLAN_CELL), gh = Math.ceil(g.height / PLAN_CELL);
  const blocked = new Uint8Array(gw * gh);
  for (let gy = 0; gy < gh; gy++) {
    for (let gx = 0; gx < gw; gx++) {
      let walls = 0, unknown = 0, total = 0;
      for (let y = gy * PLAN_CELL; y < Math.min(g.height, (gy + 1) * PLAN_CELL); y++) {
        for (let x = gx * PLAN_CELL; x < Math.min(g.width, (gx + 1) * PLAN_CELL); x++) {
          const v = g.data[y * g.width + x];
          total++;
          if (v >= 65) walls++;
          else if (v < 0) unknown++;
        }
      }
      // SLAM maps are speckled with unknown cells in open floor, so only a
      // wall or a mostly unknown cell blocks the planner.
      blocked[gy * gw + gx] = walls > 0 || unknown > total * 0.6 ? 1 : 0;
    }
  }
  const inflated = new Uint8Array(blocked);
  for (let gy = 0; gy < gh; gy++) {
    for (let gx = 0; gx < gw; gx++) {
      if (!blocked[gy * gw + gx]) continue;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const x = gx + dx, y = gy + dy;
          if (x >= 0 && y >= 0 && x < gw && y < gh) inflated[y * gw + x] = 1;
        }
      }
    }
  }
  g._planner = { version: g.version, w: gw, h: gh, blocked: inflated };
  return g._planner;
}

export function planPath(g, from, to) {
  const straight = [{ x: from.x, y: from.y }, { x: to.x, y: to.y }];
  if (!g) return straight;
  const P = plannerGrid(g);
  const cs = g.resolution * PLAN_CELL;
  const toCell = (p) => [Math.floor((p.x - g.origin.x) / cs), Math.floor((p.y - g.origin.y) / cs)];
  const toWorld = (cx, cy) => ({ x: g.origin.x + (cx + 0.5) * cs, y: g.origin.y + (cy + 0.5) * cs });
  const [sx, sy] = toCell(from);
  const [tx, ty] = toCell(to);
  const { w, h, blocked } = P;
  const inside = (x, y) => x >= 0 && y >= 0 && x < w && y < h;
  if (!inside(sx, sy) || !inside(tx, ty)) return straight;
  // Start/goal often sit inside inflated space (under a rack, at the dock).
  const near = (x, y, cx, cy) => Math.abs(x - cx) <= 4 && Math.abs(y - cy) <= 4;
  const free = (x, y) => inside(x, y) && (!blocked[y * w + x] || near(x, y, tx, ty) || near(x, y, sx, sy));

  const n = w * h;
  const gs = new Float32Array(n).fill(Infinity);
  const came = new Int32Array(n).fill(-1);
  const closed = new Uint8Array(n);
  const heap = [];
  const push = (i, f) => {
    heap.push([f, i]);
    let k = heap.length - 1;
    while (k > 0) {
      const p = (k - 1) >> 1;
      if (heap[p][0] <= heap[k][0]) break;
      [heap[p], heap[k]] = [heap[k], heap[p]];
      k = p;
    }
  };
  const pop = () => {
    const top = heap[0];
    const last = heap.pop();
    if (heap.length) {
      heap[0] = last;
      let k = 0;
      for (;;) {
        const l = 2 * k + 1, r = l + 1;
        let m = k;
        if (l < heap.length && heap[l][0] < heap[m][0]) m = l;
        if (r < heap.length && heap[r][0] < heap[m][0]) m = r;
        if (m === k) break;
        [heap[m], heap[k]] = [heap[k], heap[m]];
        k = m;
      }
    }
    return top;
  };
  const hc = (x, y) => Math.hypot(x - tx, y - ty);
  const start = sy * w + sx, goal = ty * w + tx;
  gs[start] = 0;
  push(start, hc(sx, sy));
  const dirs = [[1, 0, 1], [-1, 0, 1], [0, 1, 1], [0, -1, 1], [1, 1, Math.SQRT2], [1, -1, Math.SQRT2], [-1, 1, Math.SQRT2], [-1, -1, Math.SQRT2]];
  let found = false;
  while (heap.length) {
    const [, i] = pop();
    if (closed[i]) continue;
    if (i === goal) { found = true; break; }
    closed[i] = 1;
    const x = i % w, y = (i / w) | 0;
    for (const [dx, dy, c] of dirs) {
      const nx = x + dx, ny = y + dy;
      if (!free(nx, ny)) continue;
      const j = ny * w + nx;
      const ng = gs[i] + c;
      if (ng < gs[j]) { gs[j] = ng; came[j] = i; push(j, ng + hc(nx, ny)); }
    }
  }
  if (!found) return straight;
  const cells = [];
  for (let i = goal; i !== -1; i = came[i]) cells.push(i);
  cells.reverse();
  const pts = [{ x: from.x, y: from.y }];
  for (let k = 1; k < cells.length - 1; k += 2) pts.push(toWorld(cells[k] % w, (cells[k] / w) | 0));
  pts.push({ x: to.x, y: to.y });
  return chaikin(pts, 2);
}

function chaikin(pts, passes) {
  let out = pts;
  for (let p = 0; p < passes && out.length > 3; p++) {
    const next = [out[0]];
    for (let i = 0; i < out.length - 1; i++) {
      const a = out[i], b = out[i + 1];
      next.push({ x: a.x * 0.75 + b.x * 0.25, y: a.y * 0.75 + b.y * 0.25 });
      next.push({ x: a.x * 0.25 + b.x * 0.75, y: a.y * 0.25 + b.y * 0.75 });
    }
    next.push(out[out.length - 1]);
    out = next;
  }
  return out;
}
