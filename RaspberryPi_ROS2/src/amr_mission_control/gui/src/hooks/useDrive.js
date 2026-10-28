import { useState, useEffect, useRef, useCallback } from "react";
import { TELEOP_LINEAR, TELEOP_ANGULAR } from "../lib/geometry";

const KEYS = ["q", "w", "e", "a", "s", "d"];
// Differential drive: W/S drive, A/D (or Q/E) turn in place. The backend ignores sideways motion.
const TURN_LEFT = new Set(["a", "q"]);
const TURN_RIGHT = new Set(["d", "e"]);

function isTyping() {
  const el = document.activeElement;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable ||
    !!el.closest?.('[role="listbox"],[role="menu"],[role="dialog"],[role="alertdialog"],[role="toolbar"].editbar');
}

/**
 * Hold-to-drive, app-wide: W A S D work from any tab or layout (essential
 * while mapping). Publishes at 10 Hz while held and one zero on release.
 */
export function useDrive(sendCommand, disabled) {
  const [held, setHeld] = useState(() => new Set());
  const wasMoving = useRef(false);

  const publish = useCallback((keys) => {
    let lx = 0, az = 0;
    if (keys.has("w")) lx += TELEOP_LINEAR;
    if (keys.has("s")) lx -= TELEOP_LINEAR;
    if ([...keys].some((k) => TURN_LEFT.has(k))) az += TELEOP_ANGULAR;
    if ([...keys].some((k) => TURN_RIGHT.has(k))) az -= TELEOP_ANGULAR;
    const moving = lx || az;
    if (moving || wasMoving.current) sendCommand({ action: "teleop", lx, ly: 0, az });
    wasMoving.current = !!moving;
  }, [sendCommand]);

  const press = useCallback((k) => setHeld((p) => (p.has(k) ? p : new Set(p).add(k))), []);
  const release = useCallback((k) => setHeld((p) => {
    if (!p.has(k)) return p;
    const n = new Set(p);
    n.delete(k);
    return n;
  }), []);

  useEffect(() => {
    if (disabled) return undefined;
    const down = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping()) return;
      const k = e.key.toLowerCase();
      if (KEYS.includes(k)) { e.preventDefault(); press(k); }
    };
    const up = (e) => {
      const k = e.key.toLowerCase();
      if (KEYS.includes(k)) release(k);
    };
    const blur = () => setHeld(new Set());
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", blur);
    };
  }, [disabled, press, release]);

  useEffect(() => {
    publish(held);
    if (!held.size) return undefined;
    const id = setInterval(() => publish(held), 100);
    return () => clearInterval(id);
  }, [held, publish]);

  useEffect(() => { if (disabled) setHeld(new Set()); }, [disabled]);

  return { held, press, release };
}
