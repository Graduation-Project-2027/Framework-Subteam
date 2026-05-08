/**
 * Header — Top control bar with state badge, mission controls, connection indicator.
 */
export default function Header({ missionState, connected, sendCommand }) {
  const state = missionState.state;

  const badgeClass = `badge badge--${state.toLowerCase()}`;

  return (
    <header className="header">
      <div className="header__logo">
        <div className="header__logo-icon">🤖</div>
        <h1>AGV Mission Control</h1>
      </div>

      <div className="header__controls">
        <button
          className="btn btn--success btn--sm"
          onClick={() => sendCommand({ action: "start" })}
          disabled={state !== "IDLE"}
        >
          ▶ Start
        </button>
        <button
          className="btn btn--warning btn--sm"
          onClick={() =>
            sendCommand({ action: state === "PAUSED" ? "resume" : "pause" })
          }
          disabled={state === "IDLE" || state === "ERROR"}
        >
          {state === "PAUSED" ? "⏵ Resume" : "⏸ Pause"}
        </button>
        <button
          className="btn btn--sm"
          onClick={() => sendCommand({ action: "stop" })}
          disabled={state === "IDLE"}
        >
          ⏹ Stop
        </button>
        <button
          className="btn btn--danger btn--sm"
          onClick={() => sendCommand({ action: "estop" })}
        >
          ⚡ E-Stop
        </button>
        <button
          className="btn btn--sm"
          onClick={() => sendCommand({ action: "return_to_dock" })}
        >
          🏠 Dock
        </button>
      </div>

      <div className="header__status">
        <span className={badgeClass}>
          <span className="badge__dot" />
          {state}
        </span>
        <span
          className={`conn-dot ${connected ? "conn-dot--connected" : "conn-dot--disconnected"}`}
          title={connected ? "Connected" : "Disconnected"}
        />
      </div>
    </header>
  );
}
