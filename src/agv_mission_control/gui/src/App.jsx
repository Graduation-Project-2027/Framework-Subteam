import { Component, useState, useCallback } from "react";
import { useROS } from "./hooks/useROS";
import Header from "./components/Header";
import MapCanvas from "./components/MapCanvas";
import LocationEditor from "./components/LocationEditor";
import TaskQueue from "./components/TaskQueue";
import Teleop from "./components/Teleop";
import FeedbackConsole from "./components/FeedbackConsole";
import "./index.css";

/* ── Error Boundary — prevents crashes from blanking the entire UI ──── */
class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          padding: 40, textAlign: "center",
          background: "var(--bg-primary)", color: "var(--text-primary)",
          height: "100vh", display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "center", gap: 16,
        }}>
          <div style={{ fontSize: 48 }}>⚠️</div>
          <h2>Something went wrong</h2>
          <p style={{ color: "var(--text-secondary)", maxWidth: 500, fontSize: 14 }}>
            {String(this.state.error)}
          </p>
          <button
            className="btn btn--primary"
            onClick={() => { this.setState({ hasError: false, error: null }); }}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

/* ── Safe accessor helpers ─────────────────────────────────────────── */
function safeNum(v, fallback = 0) {
  return typeof v === "number" && isFinite(v) ? v : fallback;
}

/* ── Speed Slider Component ────────────────────────────────────────── */
function SpeedControl({ sendCommand }) {
  const [speed, setSpeed] = useState(0.4);

  const handleChange = (e) => {
    const val = parseFloat(e.target.value);
    setSpeed(val);
  };

  const handleCommit = () => {
    sendCommand({ action: "set_speed", speed });
  };

  return (
    <div className="speed-control">
      <span className="speed-control__label">⚡ Speed</span>
      <input
        type="range"
        min="0.1"
        max="1.0"
        step="0.05"
        value={speed}
        onChange={handleChange}
        onMouseUp={handleCommit}
        onTouchEnd={handleCommit}
        className="speed-control__slider"
      />
      <span className="speed-control__value">{speed.toFixed(2)} m/s</span>
    </div>
  );
}

/* ── Main App ──────────────────────────────────────────────────────── */
function AppInner() {
  const { connected, missionState, feedbackLog, sendCommand } = useROS();
  const [pickMode, setPickMode] = useState(null); // null | { active, callback }
  const [laneDraft, setLaneDraft] = useState(null); // null | { points: [{x,y}, ...] }

  // Null-safe destructuring of mission state
  const robotPose = missionState?.robot_pose || { x: 0, y: 0, yaw: 0 };
  const state = missionState?.state || "IDLE";
  const currentTask = missionState?.current_task;
  const currentStep = missionState?.current_step || "";
  const progress = safeNum(missionState?.progress, 0);

  const handlePickPosition = useCallback((pos) => {
    if (!pickMode) return;
    if (pickMode.callback) {
      pickMode.callback(pos);
    }
    setPickMode(null);
  }, [pickMode]);

  const startPickMode = useCallback((callback) => {
    setPickMode({ active: true, callback });
  }, []);

  // ── Lane drawing ──────────────────────────────────────────────────
  const startLaneDraft = useCallback(() => {
    setLaneDraft({ points: [] });
  }, []);

  const addLanePoint = useCallback((x, y) => {
    setLaneDraft((d) => (d ? { ...d, points: [...d.points, { x, y }] } : d));
  }, []);

  const finishLaneDraft = useCallback(() => {
    setLaneDraft((d) => {
      if (d && d.points.length >= 2) {
        const existing = missionState?.locations?.lanes || [];
        const newLane = {
          id: `lane_${Date.now()}`,
          name: `Lane ${existing.length + 1}`,
          points: d.points,
        };
        sendCommand({ action: "update_lanes", lanes: [...existing, newLane] });
      }
      return null;
    });
  }, [missionState, sendCommand]);

  const cancelLaneDraft = useCallback(() => setLaneDraft(null), []);

  const deleteLane = useCallback((laneId) => {
    const remaining = (missionState?.locations?.lanes || [])
      .filter((l) => l.id !== laneId);
    sendCommand({ action: "update_lanes", lanes: remaining });
  }, [missionState, sendCommand]);

  return (
    <div className="app">
      <Header missionState={missionState} connected={connected} sendCommand={sendCommand} />

      <div className="sidebar-left">
        <LocationEditor
          missionState={missionState}
          sendCommand={sendCommand}
          onPickFromMap={startPickMode}
          onStartLaneDraft={startLaneDraft}
          laneDraftActive={!!laneDraft}
          onFinishLaneDraft={finishLaneDraft}
          onCancelLaneDraft={cancelLaneDraft}
          onDeleteLane={deleteLane}
        />
        <TaskQueue missionState={missionState} sendCommand={sendCommand} />
      </div>

      <MapCanvas
        missionState={missionState}
        sendCommand={sendCommand}
        pickMode={!!pickMode}
        onPickPosition={handlePickPosition}
        laneDraft={laneDraft}
        onAddLanePoint={addLanePoint}
        onFinishLaneDraft={finishLaneDraft}
      />

      <div className="sidebar-right panel">
        <div className="panel__header">
          <span className="panel__title">ℹ Robot Info</span>
        </div>
        <div className="panel__body" style={{ padding: "12px 16px", fontSize: 13 }}>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>Position</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              ({safeNum(robotPose.x).toFixed(2)}, {safeNum(robotPose.y).toFixed(2)})
            </span>
          </div>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>Heading</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {(safeNum(robotPose.yaw) * 180 / Math.PI).toFixed(1)}°
            </span>
          </div>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>State</span>
            <span style={{ fontWeight: 600 }}>{state}</span>
          </div>
          {currentTask && (
            <>
              <div className="divider" />
              <div style={{ marginBottom: 4 }}>
                <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>Current Task</span>
              </div>
              <div style={{ fontWeight: 500 }}>
                {currentTask.from_name
                  ? `${currentTask.from_name} → ${currentTask.to_name}`
                  : currentTask.to_name || "—"}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                Step: {currentStep}
              </div>
              <div className="progress" style={{ marginTop: 8 }}>
                <div className="progress__fill" style={{ width: `${progress * 100}%` }} />
              </div>
            </>
          )}
        </div>
      </div>

      <div className="bottom-bar">
        <Teleop sendCommand={sendCommand} />
        <SpeedControl sendCommand={sendCommand} />
        <FeedbackConsole feedbackLog={feedbackLog} />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <ErrorBoundary>
      <AppInner />
    </ErrorBoundary>
  );
}
