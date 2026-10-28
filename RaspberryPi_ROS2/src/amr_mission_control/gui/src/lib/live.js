/**
 * Live store for high-rate robot data (maps, costmaps, scans, TF).
 *
 * Kept out of React state on purpose: the map canvas reads it every frame,
 * and React components that need a summary subscribe with useLiveSelector.
 */
import { useSyncExternalStore } from "react";
import { TFBuffer, apply } from "./tf";
import { valueAt } from "./grid";

export function createLive() {
  const listeners = new Set();
  let notifyQueued = false;
  const live = {
    map: null,          // /map (SLAM: growing in mapping, pose-graph map in navigation)
    mapStatic: null,    // /map_static (saved, possibly edited image used for planning)
    mapFile: null,      // saved map image fetched from the launcher
    globalCostmap: null,
    localCostmap: null,
    scans: { merged: null, front: null, rear: null },
    dynamic: [],
    rackCloud: null,
    footprint: null,
    tf: new TFBuffer(),
    seen: {},           // topic -> last receive time (ms)
    rev: 0,
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
    // Coalesce UI notifications to one per animation frame.
    notify() {
      live.rev += 1;
      if (notifyQueued) return;
      notifyQueued = true;
      requestAnimationFrame(() => {
        notifyQueued = false;
        listeners.forEach((fn) => fn());
      });
    },
    reset() {
      live.map = live.mapStatic = live.globalCostmap = live.localCostmap = live.rackCloud = live.footprint = null;
      live.scans = { merged: null, front: null, rear: null };
      live.dynamic = [];
      live.seen = {};
      live.tf.clear();
      live.notify();
    },
  };
  return live;
}

export function useLiveSelector(live, select) {
  return useSyncExternalStore(live.subscribe, () => select(live));
}

/** Background map shown under everything, by priority. */
export function displayMap(live, mode) {
  if (mode === "mapping") return live.map;
  return live.mapStatic || live.mapFile || live.map;
}

/** sensor_msgs/LaserScan → flat [x0, y0, x1, y1, …] in the map frame. */
export function scanToMap(msg, tf, ranges) {
  const frame = (msg.header?.frame_id || "").replace(/^\//, "");
  const T = tf.lookup("map", frame);
  if (!T) return null;
  const out = new Float32Array(ranges.length * 2);
  let n = 0;
  const { angle_min: a0, angle_increment: inc, range_min: rmin, range_max: rmax } = msg;
  for (let i = 0; i < ranges.length; i++) {
    const r = ranges[i];
    if (!(r > rmin && r < rmax)) continue;
    const a = a0 + i * inc;
    const [x, y] = apply(T, r * Math.cos(a), r * Math.sin(a));
    out[n++] = x;
    out[n++] = y;
  }
  return out.subarray(0, n);
}

/**
 * Lidar hits that the map can't explain (on free floor, away from walls),
 * clustered into boxes: people, carts, anything that moved in.
 */
export function detectDynamic(points, map, cluster = 0.3) {
  if (!points || !map) return [];
  const margin = 0.15;
  const buckets = new Map();
  for (let i = 0; i < points.length; i += 2) {
    const x = points[i], y = points[i + 1];
    if (valueAt(map, x, y) !== 0) continue;
    let explained = false;
    for (let dx = -margin; dx <= margin && !explained; dx += 0.05) {
      for (let dy = -margin; dy <= margin; dy += 0.05) {
        if (valueAt(map, x + dx, y + dy) !== 0) { explained = true; break; }
      }
    }
    if (explained) continue;
    const key = `${Math.floor(x / cluster)},${Math.floor(y / cluster)}`;
    const b = buckets.get(key) || { x0: x, y0: y, x1: x, y1: y, n: 0 };
    b.x0 = Math.min(b.x0, x); b.y0 = Math.min(b.y0, y); b.x1 = Math.max(b.x1, x); b.y1 = Math.max(b.y1, y); b.n += 1;
    buckets.set(key, b);
  }
  // merge neighbouring buckets
  const boxes = [...buckets.values()].filter((b) => b.n >= 2);
  const merged = [];
  for (const b of boxes) {
    const hit = merged.find((m) => b.x0 <= m.x1 + cluster && b.x1 >= m.x0 - cluster && b.y0 <= m.y1 + cluster && b.y1 >= m.y0 - cluster);
    if (hit) {
      hit.x0 = Math.min(hit.x0, b.x0); hit.y0 = Math.min(hit.y0, b.y0);
      hit.x1 = Math.max(hit.x1, b.x1); hit.y1 = Math.max(hit.y1, b.y1); hit.n += b.n;
    } else merged.push({ ...b });
  }
  return merged.filter((m) => m.n >= 3);
}
