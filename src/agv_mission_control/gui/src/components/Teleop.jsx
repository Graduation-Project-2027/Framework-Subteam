/**
 * Teleop — WASD keyboard controls + lift buttons.
 * Publishes cmd_vel via the mission_control/command topic.
 */
import { useState, useEffect, useCallback, useRef } from "react";

const SPEED = 0.4;
const ANGULAR = 0.8;

export default function Teleop({ sendCommand }) {
  const [activeKeys, setActiveKeys] = useState(new Set());
  const intervalRef = useRef(null);

  const sendTeleop = useCallback(
    (keys) => {
      let lx = 0, ly = 0, az = 0;
      if (keys.has("w")) lx += SPEED;
      if (keys.has("s")) lx -= SPEED;
      if (keys.has("a")) ly += SPEED;
      if (keys.has("d")) ly -= SPEED;
      if (keys.has("q")) az += ANGULAR;
      if (keys.has("e")) az -= ANGULAR;
      sendCommand({ action: "teleop", lx, ly, az });
    },
    [sendCommand]
  );

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

  // Publish at 10Hz when keys are pressed
  useEffect(() => {
    if (activeKeys.size > 0) {
      if (!intervalRef.current) {
        intervalRef.current = setInterval(() => sendTeleop(activeKeys), 100);
      }
    } else {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
        sendTeleop(new Set()); // stop
      }
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [activeKeys, sendTeleop]);

  const isActive = (k) => activeKeys.has(k);

  const keyBtn = (label, key, row, col) => (
    <button
      key={key || label}
      className={`teleop__key ${key && isActive(key) ? "teleop__key--active" : ""} ${!key ? "teleop__key--empty" : ""}`}
      style={{ gridRow: row, gridColumn: col }}
      onMouseDown={() => key && setActiveKeys((p) => new Set(p).add(key))}
      onMouseUp={() => key && setActiveKeys((p) => { const n = new Set(p); n.delete(key); return n; })}
      onMouseLeave={() => key && setActiveKeys((p) => { const n = new Set(p); n.delete(key); return n; })}
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
          <button className="btn btn--sm" onMouseDown={() => sendCommand({ action: "lift", height: 1.0 })}>
            ⬆ Lift Up
          </button>
          <button className="btn btn--sm" onMouseDown={() => sendCommand({ action: "lift", height: 0.0 })}>
            ⬇ Lift Down
          </button>
        </div>
      </div>
    </div>
  );
}
