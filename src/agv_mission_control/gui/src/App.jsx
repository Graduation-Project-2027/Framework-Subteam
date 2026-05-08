import { useROS } from "./hooks/useROS";
import Header from "./components/Header";
import MapCanvas from "./components/MapCanvas";
import LocationEditor from "./components/LocationEditor";
import TaskQueue from "./components/TaskQueue";
import Teleop from "./components/Teleop";
import FeedbackConsole from "./components/FeedbackConsole";
import "./index.css";

export default function App() {
  const { connected, missionState, feedbackLog, sendCommand } = useROS();

  return (
    <div className="app">
      <Header missionState={missionState} connected={connected} sendCommand={sendCommand} />

      <div className="sidebar-left">
        <LocationEditor missionState={missionState} sendCommand={sendCommand} />
        <TaskQueue missionState={missionState} sendCommand={sendCommand} />
      </div>

      <MapCanvas missionState={missionState} sendCommand={sendCommand} />

      <div className="sidebar-right panel">
        <div className="panel__header">
          <span className="panel__title">ℹ Robot Info</span>
        </div>
        <div className="panel__body" style={{ padding: "12px 16px", fontSize: 13 }}>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>Position</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              ({missionState.robot_pose?.x?.toFixed(2)}, {missionState.robot_pose?.y?.toFixed(2)})
            </span>
          </div>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>Heading</span>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {((missionState.robot_pose?.yaw || 0) * 180 / Math.PI).toFixed(1)}°
            </span>
          </div>
          <div style={{ marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "var(--text-secondary)" }}>State</span>
            <span style={{ fontWeight: 600 }}>{missionState.state}</span>
          </div>
          {missionState.current_task && (
            <>
              <div className="divider" />
              <div style={{ marginBottom: 4 }}>
                <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>Current Task</span>
              </div>
              <div style={{ fontWeight: 500 }}>
                {missionState.current_task.from_name
                  ? `${missionState.current_task.from_name} → ${missionState.current_task.to_name}`
                  : missionState.current_task.to_name}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                Step: {missionState.current_step}
              </div>
              <div className="progress" style={{ marginTop: 8 }}>
                <div className="progress__fill" style={{ width: `${(missionState.progress || 0) * 100}%` }} />
              </div>
            </>
          )}
        </div>
      </div>

      <div className="bottom-bar">
        <Teleop sendCommand={sendCommand} />
        <FeedbackConsole feedbackLog={feedbackLog} />
      </div>
    </div>
  );
}
