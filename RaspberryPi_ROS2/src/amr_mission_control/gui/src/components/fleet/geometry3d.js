/* Geometry helpers for the fleet scene. World is the map frame, Z up:
   (x, y) metres on the floor, z height. */
import * as THREE from "three";
import { knownBounds } from "../../lib/grid";

/** Flat strip of `width` along a polyline, at height z (triangle list). */
export function ribbon(points, width, z = 0) {
  const pts = points.map((p) => (Array.isArray(p) ? { x: p[0], y: p[1] } : p));
  const pos = [];
  const h = width / 2;
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    const d = Math.hypot(b.x - a.x, b.y - a.y);
    if (d < 1e-4) continue;
    const nx = (-(b.y - a.y) / d) * h, ny = ((b.x - a.x) / d) * h;
    const A1 = [a.x + nx, a.y + ny, z], A2 = [a.x - nx, a.y - ny, z];
    const B1 = [b.x + nx, b.y + ny, z], B2 = [b.x - nx, b.y - ny, z];
    pos.push(...A1, ...A2, ...B1, ...B1, ...A2, ...B2);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.computeVertexNormals();
  return g;
}

/** Union of everything the fleet uses, padded; falls back to the known map. */
export function contentBounds(grid, layout, robots) {
  const xs = [], ys = [];
  const add = (p) => { if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) { xs.push(p.x); ys.push(p.y); } };
  (layout?.racks || []).forEach(add);
  (layout?.drop_zones || []).forEach(add);
  (layout?.lanes || []).forEach((l) => l.points.forEach(add));
  robots.forEach((r) => add(r.mission?.pose));
  if (xs.length >= 2) {
    const pad = 2;
    return { x0: Math.min(...xs) - pad, x1: Math.max(...xs) + pad, y0: Math.min(...ys) - pad, y1: Math.max(...ys) + pad };
  }
  if (grid) return knownBounds(grid) || { x0: grid.origin.x, y0: grid.origin.y, x1: grid.origin.x + grid.width * grid.resolution, y1: grid.origin.y + grid.height * grid.resolution };
  return { x0: -6, x1: 6, y0: -6, y1: 6 };
}

const PX = 2; // texture pixels per map cell

/** Paper floor: free = paper, unknown = ground, walls = ink, 1 m / 5 m rules. */
export function floorTexture(grid, b) {
  const r = grid.resolution;
  const cx0 = Math.max(0, Math.floor((b.x0 - grid.origin.x) / r));
  const cy0 = Math.max(0, Math.floor((b.y0 - grid.origin.y) / r));
  const cx1 = Math.min(grid.width, Math.ceil((b.x1 - grid.origin.x) / r));
  const cy1 = Math.min(grid.height, Math.ceil((b.y1 - grid.origin.y) / r));
  const w = Math.max(1, cx1 - cx0), h = Math.max(1, cy1 - cy0);
  const small = document.createElement("canvas");
  small.width = w;
  small.height = h;
  const sctx = small.getContext("2d");
  const img = sctx.createImageData(w, h);
  for (let row = 0; row < h; row++) {
    const gy = cy1 - 1 - row;
    for (let x = 0; x < w; x++) {
      const v = grid.data[gy * grid.width + cx0 + x];
      const o = (row * w + x) * 4;
      const [R, G, B] = v < 0 ? [236, 237, 240] : v >= 65 ? [46, 48, 53] : [255, 255, 255];
      img.data[o] = R; img.data[o + 1] = G; img.data[o + 2] = B; img.data[o + 3] = 255;
    }
  }
  sctx.putImageData(img, 0, 0);
  const c = document.createElement("canvas");
  c.width = w * PX;
  c.height = h * PX;
  const ctx = c.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(small, 0, 0, c.width, c.height);
  const wx0 = grid.origin.x + cx0 * r, wy1 = grid.origin.y + cy1 * r;
  const toX = (x) => ((x - wx0) / r) * PX, toY = (y) => ((wy1 - y) / r) * PX;
  for (let gx = Math.ceil(wx0); gx <= wx0 + w * r; gx++) {
    ctx.fillStyle = gx % 5 === 0 ? "rgba(20,20,22,0.13)" : "rgba(20,20,22,0.055)";
    ctx.fillRect(Math.round(toX(gx)), 0, 1, c.height);
  }
  for (let gy = Math.ceil(wy1 - h * r); gy <= wy1; gy++) {
    ctx.fillStyle = gy % 5 === 0 ? "rgba(20,20,22,0.13)" : "rgba(20,20,22,0.055)";
    ctx.fillRect(0, Math.round(toY(gy)), c.width, 1);
  }
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  tex.magFilter = THREE.LinearFilter;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  return { texture: tex, x0: wx0, y0: wy1 - h * r, x1: wx0 + w * r, y1: wy1 };
}

/** Occupied cells merged into row runs: [x, y, length] in metres. */
export function wallRuns(grid, b) {
  const r = grid.resolution;
  const cx0 = Math.max(0, Math.floor((b.x0 - grid.origin.x) / r));
  const cy0 = Math.max(0, Math.floor((b.y0 - grid.origin.y) / r));
  const cx1 = Math.min(grid.width, Math.ceil((b.x1 - grid.origin.x) / r));
  const cy1 = Math.min(grid.height, Math.ceil((b.y1 - grid.origin.y) / r));
  const runs = [];
  for (let y = cy0; y < cy1; y++) {
    let start = -1;
    for (let x = cx0; x <= cx1; x++) {
      const occ = x < cx1 && grid.data[y * grid.width + x] >= 65;
      if (occ && start < 0) start = x;
      if (!occ && start >= 0) {
        const len = (x - start) * r;
        runs.push([grid.origin.x + start * r + len / 2, grid.origin.y + (y + 0.5) * r, len]);
        start = -1;
      }
    }
  }
  return runs;
}
