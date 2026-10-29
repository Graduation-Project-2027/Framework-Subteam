import { memo } from "react";
import { Pause, Play, House, OctagonX, FlaskConical, X, Laptop } from "lucide-react";
import { Button, Tip, Sun } from "../ui";
import { WorkspaceNav } from "../TopBar";
import { isDriving } from "../../lib/traffic";
import { cx } from "../../lib/cx";

function ManagerStatus({ fleet, onExitPreview }) {
  if (fleet.preview) {
    return (
      <Tip label="Simulated fleet running in this browser; nothing reaches a robot. Click to exit preview.">
        <button type="button" className="pill pill--gold pill--btn" onClick={onExitPreview} aria-label="Exit preview">
          <FlaskConical size={13} strokeWidth={2} aria-hidden="true" />
          Preview
          <X size={13} strokeWidth={2} aria-hidden="true" />
        </button>
      </Tip>
    );
  }
  const map = { online: ["ok", "Fleet manager"], connecting: ["wait", "Connecting"], offline: ["off", "Manager offline"] };
  const [dot, text] = map[fleet.status] || map.offline;
  return (
    <Tip label={`fleet_server.py at ${fleet.client.url}${fleet.state.manager ? ` (${fleet.state.manager})` : ""}`}>
      <span className="link" tabIndex={0} role="status">
        <Laptop size={15} strokeWidth={1.75} aria-hidden="true" />
        <span className={cx("dot", `dot--${dot}`)} />
        <span className="hide-sm">{text}</span>
      </span>
    </Tip>
  );
}

function FleetTopBar({ fleet, workspace, onWorkspace, consoleLabel, onExitPreview, compact, onPickRobot }) {
  const robots = fleet.state.robots.filter((r) => r.adopted);
  const linked = robots.filter((r) => r.linked);
  const driving = linked.filter((r) => isDriving(r.mission) && !r.hold).length;
  const holding = linked.filter((r) => r.hold).length;
  const paused = linked.filter((r) => r.mission?.state === "PAUSED").length;
  const usable = fleet.status === "online" && linked.length > 0;
  const send = (cmd) => fleet.act((c) => c.commandAll(cmd));
  const why = fleet.status !== "online" ? "Fleet manager offline" : !linked.length ? "No robot is linked" : undefined;

  const controls = [
    paused && !driving
      ? { key: "resume", label: "Resume all", icon: Play, onClick: () => send({ action: "resume" }) }
      : { key: "pause", label: "Pause all", icon: Pause, onClick: () => send({ action: "pause" }) },
    { key: "dock", label: "Dock all", icon: House, onClick: () => send({ action: "return_to_dock" }) },
  ];

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <img src="/ejust-emblem.png" alt="E-JUST" width="22" height="28" />
        <h1 className="wordmark" translate="no">LEVERAGE<span className="sr-only"> fleet</span></h1>
      </div>
      <WorkspaceNav value={workspace} onChange={onWorkspace} consoleLabel={consoleLabel}
        robots={fleet.state.robots.filter((r) => r.adopted)} onPickRobot={onPickRobot} preview={fleet.preview} />
      <div className="topbar__status">
        <ManagerStatus fleet={fleet} onExitPreview={onExitPreview} />
      </div>

      <div className="topbar__state fleetcount" aria-live="polite">
        {usable ? (
          <>
            <span className="fleetcount__item">{driving ? <Sun live size={9} /> : <span className="dot dot--idle" />}<b className="num">{driving}</b> driving</span>
            {holding > 0 && <span className="fleetcount__item tone--caution"><span className="dot dot--caution" /><b className="num">{holding}</b> holding</span>}
            <span className="fleetcount__item hide-sm"><b className="num">{linked.length}</b>/<span className="num">{robots.length}</span> linked</span>
          </>
        ) : (
          <span className="fleetcount__item">{fleet.status === "online" ? "No robots linked" : "No fleet"}</span>
        )}
      </div>

      <div className="topbar__controls" role="group" aria-label="Whole fleet">
        {controls.map((c) => (
          <Tip key={c.key} label={compact || !usable ? (usable ? c.label : `${c.label}: ${why.toLowerCase()}`) : undefined} side="bottom">
            <span className="tipwrap">
              <Button icon={c.icon} size="sm" variant="secondary" disabled={!usable} onClick={c.onClick} aria-label={c.label}>
                {compact ? null : c.label}
              </Button>
            </span>
          </Tip>
        ))}
      </div>

      <Tip label={usable ? "Emergency stop every robot in the fleet" : why} side="bottom">
        <span className="tipwrap">
          <Button className="estop" variant="danger" icon={OctagonX} disabled={!usable} onClick={() => send({ action: "estop" })}>
            {compact ? "E-stop" : "E-stop all"}
          </Button>
        </span>
      </Tip>

    </header>
  );
}

export default memo(FleetTopBar);
