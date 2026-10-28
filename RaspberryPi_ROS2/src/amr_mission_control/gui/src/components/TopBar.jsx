import { useState, memo } from "react";
import { Play, Pause, Square, House, OctagonX, Server, ChevronRight, FlaskConical, X, ShieldCheck } from "lucide-react";
import { Button, Tip, ConfirmDialog, Sun } from "./ui";
import { stateMeta } from "../lib/mission";
import { cx } from "../lib/cx";

function StackButton({ stack, onOpen }) {
  const s = stack.status;
  let dot = "off", text = "Stack stopped";
  if (stack.reachable === false) text = "Launcher offline";
  else if (!s) text = "Checking…";
  else if (s.running && s.starting) { dot = "wait"; text = "Starting…"; }
  else if (s.running) { dot = "ok"; text = s.mode === "mapping" ? "Mapping" : `Navigation · ${s.map}`; }
  return (
    <Button variant="ghost" size="sm" className="topbar__system" onClick={onOpen} aria-label={`Robot stack: ${text}. Open system controls`}>
      <Server size={16} strokeWidth={1.75} aria-hidden="true" />
      <span className={cx("dot", `dot--${dot}`)} />
      <span className="topbar__system-text hide-sm">{text}</span>
      <ChevronRight size={14} strokeWidth={1.75} aria-hidden="true" className="hide-sm" />
    </Button>
  );
}

function LinkStatus({ mode, status, url, onExitPreview }) {
  if (mode === "preview") {
    return (
      <Tip label="Simulated robot running in this browser; commands never reach hardware. Click to exit preview.">
        <button type="button" className="pill pill--gold pill--btn" onClick={onExitPreview} aria-label="Exit preview">
          <FlaskConical size={13} strokeWidth={2} aria-hidden="true" />
          Preview
          <X size={13} strokeWidth={2} aria-hidden="true" />
        </button>
      </Tip>
    );
  }
  const map = {
    connected: { dot: "ok", text: "Connected" },
    connecting: { dot: "wait", text: "Connecting" },
    offline: { dot: "off", text: "Offline" },
  };
  const m = map[status] || map.offline;
  return (
    <Tip label={`rosbridge ${url}`}>
      <span className="link" tabIndex={0} role="status">
        <span className={cx("dot", `dot--${m.dot}`)} />
        <span className="hide-sm">{m.text}</span>
      </span>
    </Tip>
  );
}

function TopBar({ missionState, mode, status, online, url, sendCommand, onExitPreview, compact, stack, stackMode, onOpenSystem }) {
  const [releaseOpen, setReleaseOpen] = useState(false);
  const state = missionState.state;
  const meta = stateMeta(state);
  const usable = online;
  const mapping = stackMode === "mapping";
  const why = !usable ? "Connect to the robot first" : mapping ? "Mapping mode has no navigation" : undefined;
  const paused = state === "PAUSED";
  const faulted = usable && state === "ERROR";
  const navOk = usable && !mapping;

  const controls = [
    { key: "start", label: "Start mission", icon: Play, variant: "primary", disabled: !navOk || state !== "IDLE", onClick: () => sendCommand({ action: "start" }) },
    { key: "pause", label: paused ? "Resume" : "Pause", icon: paused ? Play : Pause, disabled: !navOk || state === "IDLE" || state === "ERROR", onClick: () => sendCommand({ action: paused ? "resume" : "pause" }) },
    { key: "stop", label: "Stop", icon: Square, disabled: !navOk || state === "IDLE", onClick: () => sendCommand({ action: "stop" }) },
    { key: "dock", label: "Return to dock", icon: House, disabled: !navOk, onClick: () => sendCommand({ action: "return_to_dock" }) },
  ];

  return (
    <header className="topbar">
      <div className="topbar__brand">
        <img src="/ejust-emblem.png" alt="E-JUST" width="22" height="28" />
        <h1 className="wordmark" translate="no">LEVERAGE<span className="sr-only"> mission control</span></h1>
      </div>

      <div className="topbar__status">
        <LinkStatus mode={mode} status={status} url={url} onExitPreview={onExitPreview} />
        <StackButton stack={stack} onOpen={onOpenSystem} />
      </div>

      <div className={cx("topbar__state", `tone--${mapping && usable ? "live" : meta.tone}`)} aria-live="polite">
        {(meta.tone === "live" || mapping) && usable ? <Sun live /> : <span className={cx("dot", `dot--${meta.tone}`)} />}
        <span>{!usable ? "No robot" : mapping && state === "IDLE" ? "Mapping" : meta.label}</span>
      </div>

      <div className="topbar__controls" role="group" aria-label="Mission">
        {faulted && (
          <Button size="sm" variant="secondary" icon={ShieldCheck} className="release" aria-label="Release E-stop" onClick={() => setReleaseOpen(true)}>
            <span className="hide-sm">Release E-stop</span>
          </Button>
        )}
        {controls.map((c) => (compact ? (
          <Tip key={c.key} label={c.disabled && why ? `${c.label}: ${why.toLowerCase()}` : c.label} side="bottom">
            <span className="tipwrap">
              <Button aria-label={c.label} icon={c.icon} variant={c.variant || "secondary"} size="sm" disabled={c.disabled} onClick={c.onClick} />
            </span>
          </Tip>
        ) : (
          <Tip key={c.key} label={c.disabled ? why : undefined} side="bottom">
            <span className="tipwrap">
              <Button icon={c.icon} variant={c.variant || "secondary"} size="sm" disabled={c.disabled} onClick={c.onClick}>{c.label}</Button>
            </span>
          </Tip>
        )))}
      </div>

      <Tip label={usable ? "Emergency stop: halts all motion immediately" : "Connect to the robot first"} side="bottom">
        <span className="tipwrap">
          <Button className="estop" variant="danger" icon={OctagonX} disabled={!usable} onClick={() => sendCommand({ action: "estop" })}>
            E-stop
          </Button>
        </span>
      </Tip>

      <ConfirmDialog
        open={releaseOpen}
        onOpenChange={setReleaseOpen}
        tone="primary"
        title="Release the emergency stop?"
        body="The hardware stop is released and the robot returns to idle. It can move again as soon as a task starts or someone drives it, so make sure the floor is clear. The release is refused while the physical button is pressed."
        confirmLabel="Release E-stop"
        onConfirm={() => sendCommand({ action: "estop_release" })}
      />
    </header>
  );
}

export default memo(TopBar);
