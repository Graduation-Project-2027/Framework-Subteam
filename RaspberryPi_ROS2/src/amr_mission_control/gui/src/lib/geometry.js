export const RACK_SIDE = 0.95;
export const ROBOT_LEN = 0.7;
export const ROBOT_WID = 0.64;

export const TELEOP_LINEAR = 0.4;
export const TELEOP_ANGULAR = 0.8;

export const rad2deg = (r) => (r * 180) / Math.PI;
export const deg2rad = (d) => (d * Math.PI) / 180;

export function num(v, fallback = 0) {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export function fmt(v, digits = 2) {
  return num(v).toFixed(digits);
}
