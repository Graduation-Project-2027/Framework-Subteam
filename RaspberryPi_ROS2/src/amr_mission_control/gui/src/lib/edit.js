/** Map editing primitives: paint discs and strokes into an occupancy grid. */
import { touch } from "./grid";

export const PAINT_VALUES = { wall: 100, free: 0, unknown: -1 };

export function paintDisc(g, x, y, radius, value) {
  const r = Math.max(radius, g.resolution * 0.5);
  const cx0 = Math.floor((x - r - g.origin.x) / g.resolution), cx1 = Math.floor((x + r - g.origin.x) / g.resolution);
  const cy0 = Math.floor((y - r - g.origin.y) / g.resolution), cy1 = Math.floor((y + r - g.origin.y) / g.resolution);
  let changed = 0;
  for (let cy = Math.max(0, cy0); cy <= Math.min(g.height - 1, cy1); cy++) {
    for (let cx = Math.max(0, cx0); cx <= Math.min(g.width - 1, cx1); cx++) {
      const wx = g.origin.x + (cx + 0.5) * g.resolution, wy = g.origin.y + (cy + 0.5) * g.resolution;
      if ((wx - x) ** 2 + (wy - y) ** 2 > r * r) continue;
      const i = cy * g.width + cx;
      if (g.data[i] !== value) { g.data[i] = value; changed++; }
    }
  }
  return changed;
}

export function paintStroke(g, a, b, radius, value) {
  const d = Math.hypot(b.x - a.x, b.y - a.y);
  const n = Math.max(1, Math.ceil(d / (g.resolution * 0.5)));
  let changed = 0;
  for (let k = 0; k <= n; k++) changed += paintDisc(g, a.x + ((b.x - a.x) * k) / n, a.y + ((b.y - a.y) * k) / n, radius, value);
  if (changed) touch(g);
  return changed;
}
