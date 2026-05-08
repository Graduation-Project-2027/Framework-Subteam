/**
 * Teleop — WASD keyboard controls + lift buttons.
 * Publishes cmd_vel via the mission_control/command topic.
 *
 * Design: Uses a single interval that runs whenever keys are held.
 * On release, it clears the interval and sends exactly ONE zero-stop.
 * Does NOT continuously spam zeros when idle.
 */
import { useState, useEffect, useCallback, useRef } from "react";

const SPEED = 0.4;
const ANGULAR = 0.8;

export default function Teleop({ sendCommand }) {
  const [activeKeys, setActiveKeys] = useState(new Set());
  const intervalRef = useRef(null);
  const lastNonZeroRef = useRef(false); // true if last sent command was non-zero

  // Compute and publish velocity from a given key set
  const publishVelocity = useCallback(
    (keys) => {
      let lx = 0, ly = 0, az = 0;
      if (keys.has("w")) lx += SPEED;
      if (keys.has("s")) lx -= SPEED;
      if (keys.has("a")) ly += SPEED;
      if (keys.has("d")) ly -= SPEED;
      if (keys.has("q")) az += ANGULAR;
      if (keys.has("e")) az -= ANGULAR;

      const isNonZero = lx !== 0 || ly !== 0 || az !== 0;

      // Only publish if non-zero, or if this is the first zero after movement
      if (isNonZero || lastNonZeroRef.current) {
        try {
          sendCommand({ action: "teleop", lx, ly, az });
        } catch (err) {
          console.warn("[Teleop] send error:", err);
        }
        lastNonZeroRef.current = isNonZero;
      }
    },
    [sendCommand]
  );

  // Keyboard listeners
  useEffect(() => {
    const down = (e) => {
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "q", "e"].includes(k)) {
        e.preventDefault();
        setActiveKeys((prev) => {
          const next = new Set(prev);
          next.add(k);
          return next;
        });
      }
    };
    const up = (e) => {
      const k = e.key.toLowerCase();
      if (["w", "a", "s", "d", "q", "e"].includes(k)) {
        setActiveKeys((prev) => {
          const next = new Set(prev);
          next.delete(k);
          return next;
        });
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, []);

  // Drive the interval and stop logic based on activeKeys changes
  useEffect(() => {
    if (activeKeys.size > 0) {
      // Keys are held — start publishing at 10 Hz
      // Clear any existing interval first
      if (intervalRef.current) clearInterval(intervalRef.current);
      // Publish immediately, then every 100ms
      publishVelocity(activeKeys);
      intervalRef.current = setInterval(() => {
        publishVelocity(activeKeys);
      }, 100);
    } else {
      // All keys released — stop the interval and send exactly one zero
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      // Send a single zero-velocity command (only if we were moving)
      publishVelocity(new Set());
    }

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [activeKeys, publishVelocity]);

  const isActive = (k) => activeKeys.has(k);

  const handleMouseDown = (key) => {
    if (!key) return;
    setActiveKeys((p) => { const n = new Set(p); n.add(key); return n; });
  };

  const handleMouseUp = (key) => {
    if (!key) return;
    setActiveKeys((p) => { const n = new Set(p); n.delete(key); return n; });
  };

  const keyBtn = (label, key, row, col) => (
    <button
      key={key || label}
      className={`teleop__key ${key && isActive(key) ? "teleop__key--active" : ""} ${!key ? "teleop__key--empty" : ""}`}
      style={{ gridRow: row, gridColumn: col }}
      onMouseDown={() => handleMouseDown(key)}
      onMouseUp={() => handleMouseUp(key)}
      onMouseLeave={() => handleMouseUp(key)}
    >
      {label}
    </button>
  );

  return (
    <div className="panel">
      <div className="panel__header">
        <span className="panel__title">🎮 Teleop</span>
      </div>
      <div className="teleop">
        <div className="teleop__keys">
          {keyBtn("Q", "q", 1, 1)}
          {keyBtn("W", "w", 1, 2)}
          {keyBtn("E", "e", 1, 3)}
          {keyBtn("A", "a", 2, 1)}
          {keyBtn("S", "s", 2, 2)}
          {keyBtn("D", "d", 2, 3)}
        </div>
        <div className="teleop__lift">
          <button className="btn btn--sm" onMouseDown={() => { try { sendCommand({ action: "lift", height: 1.0 }); } catch(e) {} }}>
            ⬆ Lift Up
          </button>
          <button className="btn btn--sm" onMouseDown={() => { try { sendCommand({ action: "lift", height: 0.0 }); } catch(e) {} }}>
            ⬇ Lift Down
          </button>
        </div>
      </div>
    </div>
  );
}
