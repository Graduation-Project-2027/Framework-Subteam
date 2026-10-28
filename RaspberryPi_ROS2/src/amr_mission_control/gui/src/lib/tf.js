/**
 * A tiny 2D TF buffer fed from /tf and /tf_static over rosbridge.
 * Keeps the latest transform per child frame; enough to place scans and
 * footprints in the map frame for display.
 */
const strip = (f) => (f || "").replace(/^\//, "");

function yawOf(q) {
  return Math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z));
}

export function compose(a, b) {
  const c = Math.cos(a.yaw), s = Math.sin(a.yaw);
  return { x: a.x + c * b.x - s * b.y, y: a.y + s * b.x + c * b.y, yaw: a.yaw + b.yaw };
}

export function invert(a) {
  const c = Math.cos(a.yaw), s = Math.sin(a.yaw);
  return { x: -(c * a.x + s * a.y), y: -(-s * a.x + c * a.y), yaw: -a.yaw };
}

export function apply(t, x, y) {
  const c = Math.cos(t.yaw), s = Math.sin(t.yaw);
  return [t.x + c * x - s * y, t.y + s * x + c * y];
}

export class TFBuffer {
  constructor() {
    this.frames = new Map();
  }

  clear() {
    this.frames.clear();
  }

  ingest(msg) {
    for (const t of msg.transforms || []) {
      const tr = t.transform;
      this.frames.set(strip(t.child_frame_id), {
        parent: strip(t.header.frame_id),
        x: tr.translation.x,
        y: tr.translation.y,
        yaw: yawOf(tr.rotation),
      });
    }
  }

  /** Pose of `frame` in its tree root, and that root's name. */
  toRoot(frame) {
    let pose = { x: 0, y: 0, yaw: 0 };
    let cur = strip(frame);
    for (let guard = 0; guard < 32; guard++) {
      const t = this.frames.get(cur);
      if (!t) return { root: cur, pose };
      pose = compose(t, pose);
      cur = t.parent;
    }
    return null;
  }

  /** Transform that maps points in `source` into `target`, or null. */
  lookup(target, source) {
    const s = this.toRoot(source);
    const t = this.toRoot(target);
    if (!s || !t || s.root !== t.root) return null;
    return compose(invert(t.pose), s.pose);
  }
}
