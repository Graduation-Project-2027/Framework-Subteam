import { useEffect, useRef, useState } from "react";
import { Dialog, Switch } from "radix-ui";
import { X, Power, Play, RotateCw, TriangleAlert } from "lucide-react";
import { Button, IconButton, Segmented, Select, ConfirmDialog } from "./ui";
import { useStackLogs } from "../hooks/useStack";
import { describeStack } from "../lib/stackPrefs";
import { useToast } from "../lib/toast";
import { cx } from "../lib/cx";

function useUptime(started) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!started) return undefined;
    const id = setInterval(() => tick((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, [started]);
  if (!started) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - started));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${m} min` : m ? `${m} min ${sec} s` : `${sec} s`;
}

function Logs({ lines }) {
  const ref = useRef(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <div
      className="stacklog"
      ref={ref}
      role="log"
      aria-label="Launcher output"
      tabIndex={0}
      onScroll={(e) => { const el = e.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}
    >
      {lines.length === 0 ? <p className="stacklog__empty">Output from ros2 launch appears here.</p> : lines.map((l) => (
        <div key={l.seq} className={cx("stacklog__line", /error|exception|failed|died/i.test(l.text) && "is-error")}>
          <span className="stacklog__src">{l.source}</span>
          <code>{l.text}</code>
        </div>
      ))}
    </div>
  );
}

export default function SystemSheet({ open, onOpenChange, stack, prefs, setPrefs }) {
  const toast = useToast();
  const { status, reachable, library, busy } = stack;
  const running = !!status?.running;
  const lines = useStackLogs(stack.client, open);
  const uptime = useUptime(running ? status.started_at : null);
  const usable = library.maps.filter((m) => m.has_posegraph);
  const [mode, setMode] = useState("navigation");
  const [map, setMap] = useState("");
  const [target, setTarget] = useState(prefs.target);
  const [confirmStop, setConfirmStop] = useState(false);

  const chosenMap = usable.some((m) => m.name === map) ? map : (library.default && usable.some((m) => m.name === library.default) ? library.default : usable[0]?.name || "");
  // Opening the sheet shows what is running; edits then describe the restart.
  useEffect(() => {
    if (!open) return;
    if (running) {
      setMode(status.mode);
      setTarget(status.target);
      if (status.map) setMap(status.map);
    } else setTarget(prefs.target);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, running, status?.mode, status?.map, status?.target]);

  const fail = (e) => toast({ title: "That didn't work", body: e.message, tone: "error" });
  const same = running && status.mode === mode && status.target === target && (mode === "mapping" || status.map === chosenMap);
  const opts = { target, mode, map: mode === "navigation" ? chosenMap : undefined, rviz: prefs.rviz };
  const canLaunch = mode === "mapping" || !!chosenMap;

  const go = () => (running ? stack.relaunch(opts) : stack.launch(opts))
    .then(() => toast({ title: mode === "mapping" ? "Mapping starting" : `Navigation starting on ${chosenMap}` }))
    .catch(fail);

  const toggleRviz = (on) => {
    setPrefs({ rviz: on });
    if (running) stack.rviz(on).catch(fail);
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content className="sheetpanel" aria-describedby={undefined}>
          <header className="sheetpanel__head">
            <Dialog.Title className="sheetpanel__title">Robot stack</Dialog.Title>
            <Dialog.Close asChild><IconButton label="Close" icon={X} variant="ghost" size="sm" /></Dialog.Close>
          </header>

          {reachable === false ? (
            <div className="sheetpanel__body">
              <p className="empty">
                The launcher isn’t running on the robot computer. In a sourced workspace shell run
                <br /><code translate="no">python3 src/amr_mission_control/launcher_server.py</code><br />
                and this panel can start mapping or navigation, open RViz and manage maps.
              </p>
            </div>
          ) : (
            <div className="sheetpanel__body">
              <section className={cx("stackstate", running && "is-running")} aria-live="polite">
                <span className={cx("dot", running ? (status.starting ? "dot--wait" : "dot--ok") : "dot--off")} />
                <div>
                  <p className="stackstate__title">{status ? (status.starting ? "Starting…" : describeStack(status)) : "Checking…"}</p>
                  <p className="stackstate__sub">
                    {running ? `Up ${uptime}` : "Nothing is running on the robot computer."}
                    {status?.preview && " Preview: simulated in this browser."}
                  </p>
                </div>
              </section>
              {status?.last_exit && !running && (
                <p className="notice-inline" role="alert">
                  <TriangleAlert size={14} strokeWidth={2} aria-hidden="true" />
                  The last {status.last_exit.mode} session exited with code {status.last_exit.code}. Check the output below.
                </p>
              )}

              <section className="stackform" aria-label="Launch settings">
                <div className="field">
                  <span className="field__label" id="tgt">Robot</span>
                  <Segmented
                    aria-label="Robot"
                    value={target}
                    onValueChange={(v) => { setTarget(v); setPrefs({ target: v }); }}
                    options={[{ value: "real", label: "Real robot" }, { value: "sim", label: "Simulation" }]}
                  />
                </div>
                <div className="field">
                  <span className="field__label">Mode</span>
                  <Segmented
                    aria-label="Mode"
                    value={mode}
                    onValueChange={setMode}
                    options={[{ value: "navigation", label: "Navigation" }, { value: "mapping", label: "Mapping" }]}
                  />
                </div>
                {mode === "navigation" ? (
                  <label className="field">
                    <span className="field__label">Map</span>
                    <Select
                      aria-label="Map"
                      value={chosenMap}
                      onValueChange={setMap}
                      placeholder="No map with a pose graph"
                      disabled={!usable.length}
                      options={library.maps.map((m) => ({
                        value: m.name, label: m.name, disabled: !m.has_posegraph,
                        hint: !m.has_posegraph ? "No pose graph" : m.name === library.default ? "Default" : undefined,
                      }))}
                    />
                  </label>
                ) : (
                  <p className="hint">SLAM starts from an empty map where the robot stands. Drive it through the site, then save from the map.</p>
                )}
                <div className="switchrow">
                  <label htmlFor="rviz-switch">
                    <span className="switchrow__label">Open RViz</span>
                    <span className="switchrow__hint">
                      {status && !status.display ? "This computer has no display; run RViz on a laptop." : "On the robot computer’s screen."}
                    </span>
                  </label>
                  <Switch.Root
                    id="rviz-switch"
                    className="switch"
                    checked={running ? !!status.rviz?.running : prefs.rviz}
                    disabled={!!status && !status.display}
                    onCheckedChange={toggleRviz}
                  >
                    <Switch.Thumb className="switch__thumb" />
                  </Switch.Root>
                </div>
                {status?.rviz?.error && <p className="field__error">{status.rviz.error}</p>}

                <div className="stackform__actions">
                  {running && (
                    <Button variant="danger-quiet" icon={Power} disabled={busy === "stop"} onClick={() => setConfirmStop(true)}>
                      {busy === "stop" ? "Stopping…" : "Stop"}
                    </Button>
                  )}
                  <Button
                    variant="primary"
                    icon={running ? RotateCw : Play}
                    disabled={!canLaunch || same || busy === "launch" || !status}
                    onClick={go}
                  >
                    {busy === "launch" ? "Launching…" : running ? (same ? "Running" : "Restart with these settings") : mode === "mapping" ? "Start mapping" : "Launch navigation"}
                  </Button>
                </div>
              </section>

              <section className="stackout" aria-label="Output">
                <div className="section-head"><h2>Output</h2><span className="count num">{lines.length} lines</span></div>
                <Logs lines={lines} />
              </section>
            </div>
          )}

          <ConfirmDialog
            open={confirmStop}
            onOpenChange={setConfirmStop}
            title="Stop the robot stack?"
            body="Navigation or mapping, mission control and rosbridge shut down. The robot stops and this screen disconnects until you launch again. Unsaved mapping progress is lost."
            confirmLabel="Stop stack"
            onConfirm={() => stack.stop().then(() => toast({ title: "Stack stopped" })).catch(fail)}
          />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
