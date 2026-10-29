import { useEffect, useState, memo } from "react";
import { Crosshair, SquareTerminal, Pause, Play, Square, House, OctagonX, ShieldCheck, LogOut, Video, Navigation, Upload, Power, PackageOpen } from "lucide-react";
import { Button, Slider, Tip, ConfirmDialog, Sun, Select } from "../ui";
import { RobotGlyph } from "./common";
import { robotLine, robotState, robotTone } from "../../lib/fleetStatus";
import { stateMeta, taskTitle, taskKind } from "../../lib/mission";
import { ROBOT_ID_RE, ROBOT_ID_HINT } from "../../lib/fleetClient";
import { fmt, rad2deg } from "../../lib/geometry";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

const since = (t) => {
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min`;
};

function Settings({ robot, robots, fleet }) {
  const toast = useToast();
  const base = robot.settings || {};
  const [form, setForm] = useState(base);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const key = JSON.stringify(base);
  useEffect(() => { setForm(robot.settings || {}); setError(null); }, [robot.key, key]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v }));
  const changed = Object.keys(form).filter((k) => String(form[k] ?? "") !== String(base[k] ?? ""));
  const id = String(form.id || "").trim();
  const clash = robots.find((r) => r.key !== robot.key && r.id && r.id.toLowerCase() === id.toLowerCase());
  const domainClash = form.domain_id !== "" && form.domain_id != null
    && robots.find((r) => r.key !== robot.key && r.adopted && String(r.settings?.domain_id) === String(form.domain_id));
  const valid = ROBOT_ID_RE.test(id) && !clash;

  const save = async (e) => {
    e.preventDefault();
    if (!valid || !changed.length) return;
    const body = {};
    changed.forEach((k) => {
      body[k] = k === "domain_id" ? (form[k] === "" || form[k] == null ? null : Number(form[k]))
        : k === "priority" ? Number(form[k]) : k === "max_speed" ? Number(form[k]) : form[k];
    });
    setBusy(true);
    setError(null);
    try {
      await fleet.act((c) => c.settings(robot.key, body));
      toast({ title: `${id} saved`, body: "domain_id" in body ? "The new ROS domain applies when the stack next starts." : undefined });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form settings" onSubmit={save} aria-labelledby="settings-h">
      <div className="section-head">
        <h2 id="settings-h">Settings</h2>
        <span className="count">on the robot</span>
      </div>
      <div className="form__pair">
        <label className="field">
          <span className="field__label">Robot ID</span>
          <input className="input" value={form.id || ""} onChange={(e) => set("id")(e.target.value)} spellCheck={false} aria-invalid={!valid} />
        </label>
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={form.name || ""} maxLength={24} onChange={(e) => set("name")(e.target.value)} placeholder="Optional" />
        </label>
      </div>
      {!ROBOT_ID_RE.test(id) && <span className="field__error">{ROBOT_ID_HINT}</span>}
      {clash && <span className="field__error">{clash.label} already uses this ID.</span>}

      <div className="field">
        <span className="field__label" id="spd-l">Max speed <span className="field__value num">{fmt(form.max_speed ?? 0.4, 2)} m/s</span></span>
        <Slider value={Number(form.max_speed ?? 0.4)} min={0.1} max={1} step={0.05} onValueChange={(v) => set("max_speed")(Math.round(v * 100) / 100)} aria-label="Max speed" />
      </div>

      <div className="form__pair">
        <label className="field">
          <span className="field__label">Traffic priority</span>
          <input className="input num" type="number" min={-9} max={9} step={1} value={form.priority ?? 0}
            onChange={(e) => set("priority")(e.target.value === "" ? 0 : Math.max(-9, Math.min(9, Math.round(+e.target.value))))} />
        </label>
        <label className="field">
          <span className="field__label">ROS domain</span>
          <input className="input num" inputMode="numeric" value={form.domain_id ?? ""} placeholder="Default"
            onChange={(e) => set("domain_id")(e.target.value.replace(/\D/g, "").slice(0, 3))} />
        </label>
      </div>
      <span className={cx("hint", domainClash && "hint--warn")}>
        {domainClash
          ? `${domainClash.label} already uses domain ${form.domain_id}; their topics would mix.`
          : "Higher priority goes first where paths cross. The ROS domain applies when the stack next starts."}
      </span>

      <label className="field">
        <span className="field__label">Notes</span>
        <textarea className="input input--area" rows={2} maxLength={400} value={form.notes || ""} onChange={(e) => set("notes")(e.target.value)} placeholder="Battery swapped 12 Oct, left caster squeaks…" />
      </label>
      {error && <p className="notice-inline">{error}</p>}
      <div className="form__actions">
        {changed.length > 0 && <Button variant="ghost" onClick={() => setForm(base)}>Discard</Button>}
        <Button variant="primary" type="submit" disabled={!valid || !changed.length || busy}>
          {busy ? "Saving…" : changed.length ? `Save ${changed.length} change${changed.length > 1 ? "s" : ""}` : "Saved"}
        </Button>
      </div>
    </form>
  );
}

/** The robot's stack is stopped: start navigation on a map it has, or send
 *  it the fleet's map first. */
function StartStack({ robot, fleet }) {
  const toast = useToast();
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const own = (fleet.maps?.robots?.[robot.key] || []).filter((m) => m.has_posegraph !== false);
  const fleetMap = fleet.fleetMap;
  const lib = fleet.maps?.library.find((m) => m.name === fleetMap);
  const hasFleetCopy = own.some((m) => m.name === fleetMap && (!lib || m.fingerprint === lib.fingerprint));
  const map = own.some((m) => m.name === pick) ? pick : hasFleetCopy ? fleetMap : own[0]?.name || "";
  const run = (fn, ok) => {
    setBusy(true);
    fleet.act(fn).then(() => { toast({ title: ok }); fleet.refreshMaps(); })
      .catch((e) => toast({ title: "That didn't work", body: e.message, tone: "error" }))
      .finally(() => setBusy(false));
  };
  return (
    <section className="startstack" aria-labelledby="start-h">
      <h2 id="start-h" className="startstack__title">The stack is stopped</h2>
      {fleetMap && !hasFleetCopy && lib ? (
        <>
          <p className="hint">{robot.label} doesn’t have the fleet’s copy of <b>{fleetMap}</b>. Robots only stay apart when they localize on the same map.</p>
          <Button variant="primary" icon={Upload} disabled={busy}
            onClick={() => run((c) => c.push(fleetMap, [robot.key], true), `${fleetMap} sent to ${robot.label}`)}>
            Send {fleetMap} to {robot.label}
          </Button>
        </>
      ) : own.length ? (
        <>
          <label className="field"><span className="field__label">Map</span>
            <Select value={map} onValueChange={setPick} aria-label="Map to navigate on"
              options={own.map((m) => ({ value: m.name, label: m.name, hint: m.name === fleetMap ? "fleet floor" : undefined }))} />
          </label>
          <Button variant="primary" icon={Navigation} disabled={busy || !map}
            onClick={() => run((c) => c.launch(robot.key, map), `${robot.label}: navigation starting on ${map}`)}>
            Start navigation on {map}
          </Button>
        </>
      ) : (
        <p className="hint">This robot has no saved map yet. Send it one from the Maps tab.</p>
      )}
    </section>
  );
}

function RobotInspector({ robot, robots, fleet, onLocate, follow, onFollow, onOpenConsole, onSetup, onDeploy }) {
  const toast = useToast();
  const [confirm, setConfirm] = useState(null);
  if (!robot) {
    return (
      <div className="panel">
        <p className="empty">Select a robot on the floor or in the list to see what it is doing and change its settings.</p>
      </div>
    );
  }
  const m = robot.mission || {};
  const meta = stateMeta(m.state);
  const tone = robotTone(robot);
  const linked = robot.adopted && robot.linked;
  const send = (cmd) => fleet.act((c) => c.command(robot.key, cmd)).catch((e) => toast({ title: "Command not sent", body: e.message, tone: "error" }));
  const paused = m.state === "PAUSED";
  const faulted = m.state === "ERROR";
  const pose = m.pose;

  if (!robot.adopted) {
    return (
      <div className="panel">
        <header className="rhead">
          <RobotGlyph robot={robot} size={34} />
          <div>
            <h2 className="rhead__id">{robot.configured ? robot.label : robot.hostname || "New robot"}</h2>
            <p className="rhead__sub num">{robot.host} · {robotLine(robot)}</p>
          </div>
        </header>
        <p className="hint">Nothing is sent to this robot until it is in the fleet.</p>
        {robot.configured
          ? <Button variant="primary" onClick={() => fleet.act((c) => c.adopt(robot.key)).catch((e) => toast({ title: "Couldn't add the robot", body: e.message, tone: "error" }))}>Add to fleet</Button>
          : <Button variant="primary" onClick={() => onSetup(robot.key)}>Set up this robot</Button>}
      </div>
    );
  }

  return (
    <div className="panel panel--robot">
      <section className={cx("status", `tone--${tone}`)}>
        <header className="rhead">
          <RobotGlyph robot={robot} size={34} />
          <div className="rhead__text">
            <h2 className="rhead__id">{robot.label}{robot.name && <span className="rhead__name">{robot.name}</span>}</h2>
            <p className="rhead__sub num">{robot.hostname} · {robot.host}</p>
          </div>
        </header>
        <div className="status__state">
          {meta.tone === "live" && !robot.hold && linked ? <Sun live /> : <span className={cx("dot", `dot--${tone === "live" ? "ok" : tone}`)} />}
          {robotState(robot)}
        </div>
        <p className="status__hint">{robotLine(robot)}</p>
        {robot.hold && (
          <p className="holdnote">
            Held by fleet traffic for <b>{robot.hold.by_label}</b> ({robot.hold.reason}), {since(robot.hold.since)}. It continues the same task once the way is clear.
          </p>
        )}
        {m.task && (
          <div className="meter meter--labelled" aria-label={`${taskKind(m.task)} ${taskTitle(m.task)}`}>
            <div className="meter__bar"><i style={{ transform: `scaleX(${m.progress || 0})` }} /></div>
            <span className="num">{Math.round((m.progress || 0) * 100)}%</span>
          </div>
        )}
        <dl className="kv">
          <div><dt>Pose</dt><dd className="num">{pose ? `${fmt(pose.x)}, ${fmt(pose.y)} · ${Math.round(rad2deg(pose.yaw || 0))}°` : "—"}</dd></div>
          <div><dt>Speed cap</dt><dd className="num">{m.speed != null ? `${fmt(m.speed)} m/s` : "—"}</dd></div>
          <div><dt>Queue</dt><dd className="num">{m.queue ?? 0} task{m.queue === 1 ? "" : "s"}</dd></div>
          <div><dt>Map</dt><dd>{robot.stack?.running ? (robot.stack.mode === "mapping" ? "Mapping" : robot.stack.map) : "Stack stopped"}</dd></div>
          <div><dt>Layout</dt><dd>{robot.layout_sync === "synced" ? "In sync" : robot.layout_sync === "sending" ? "Sending…" : "Not synced"}</dd></div>
          <div><dt>Carrying</dt><dd>{m.carrying ? "Rack" : "Nothing"}</dd></div>
        </dl>
      </section>

      {!robot.stack?.running && <StartStack robot={robot} fleet={fleet} />}

      <section className="ractions" aria-label="Actions">
        <div className="ractions__row">
          <Button icon={Crosshair} variant="secondary" size="sm" onClick={() => onLocate(robot.key)} disabled={!pose}>Locate</Button>
          <Button icon={Video} variant={follow ? "primary" : "secondary"} size="sm" aria-pressed={follow} onClick={() => onFollow(follow ? null : robot.key)} disabled={!pose}>
            {follow ? "Following" : "Follow"}
          </Button>
          <Button icon={SquareTerminal} variant="secondary" size="sm" onClick={() => onOpenConsole(robot)}>Console</Button>
        </div>
        <div className="ractions__row">
          <Button icon={paused ? Play : Pause} size="sm" variant="secondary" disabled={!linked || m.state === "IDLE" || faulted} onClick={() => send({ action: paused ? "resume" : "pause" })}>{paused ? "Resume" : "Pause"}</Button>
          <Button icon={Square} size="sm" variant="secondary" disabled={!linked || m.state === "IDLE"} onClick={() => send({ action: "stop" })}>Stop</Button>
          <Button icon={House} size="sm" variant="secondary" disabled={!linked || faulted} onClick={() => send({ action: "return_to_dock" })}>Dock</Button>
        </div>
        <div className="ractions__row">
          {faulted ? (
            <Button icon={ShieldCheck} size="sm" variant="secondary" onClick={() => setConfirm("release")}>Release E-stop</Button>
          ) : (
            <Tip label={linked ? `Emergency stop ${robot.label} only` : "No rosbridge link"}>
              <span className="tipwrap"><Button icon={OctagonX} size="sm" variant="danger-quiet" disabled={!linked} onClick={() => send({ action: "estop" })}>E-stop {robot.label}</Button></span>
            </Tip>
          )}
        </div>
      </section>

      <Settings robot={robot} robots={robots} fleet={fleet} />

      <section className="danger-zone">
        {robot.stack?.running && <Button icon={Power} size="sm" variant="ghost" onClick={() => setConfirm("stop-stack")}>Stop stack</Button>}
        <Tip label={robot.stack?.running ? "Stop the stack first: deploying restarts the launcher" : "Upload this workspace to the robot, build it and restart its launcher"}>
          <span className="tipwrap">
            <Button icon={PackageOpen} size="sm" variant="ghost" disabled={robot.stack?.running} onClick={() => setConfirm("deploy")}>Deploy code</Button>
          </span>
        </Tip>
        <Button icon={LogOut} size="sm" variant="ghost" onClick={() => setConfirm("release-fleet")}>Remove from fleet</Button>
      </section>

      <ConfirmDialog
        open={confirm === "release"}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={`Release ${robot.label}'s emergency stop?`}
        body="The robot returns to idle and can move as soon as a task starts. Make sure the floor around it is clear."
        confirmLabel="Release E-stop"
        onConfirm={() => send({ action: "estop_release" })}
      />
      <ConfirmDialog
        open={confirm === "deploy"}
        onOpenChange={(o) => !o && setConfirm(null)}
        tone="primary"
        title={`Deploy the code to ${robot.label}?`}
        body="The laptop uploads this workspace over SSH, builds it on the robot and restarts its launcher. A build on the Pi takes a few minutes."
        confirmLabel="Deploy"
        onConfirm={() => onDeploy?.(robot)}
      />
      <ConfirmDialog
        open={confirm === "stop-stack"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Stop ${robot.label}'s stack?`}
        body="Navigation, the mission manager and rosbridge shut down on the robot. Any running task is cancelled."
        confirmLabel="Stop stack"
        onConfirm={() => fleet.act((c) => c.stopStack(robot.key)).catch((e) => toast({ title: "Stack not stopped", body: e.message, tone: "error" }))}
      />
      <ConfirmDialog
        open={confirm === "release-fleet"}
        onOpenChange={(o) => !o && setConfirm(null)}
        title={`Remove ${robot.label} from the fleet?`}
        body="The laptop stops managing it: no traffic holds, no shared layout, and the other robots stop seeing it on /fleet/peers. It keeps its settings and can be added again."
        confirmLabel="Remove"
        onConfirm={() => fleet.act((c) => c.release(robot.key)).then(() => toast({ title: `${robot.label} left the fleet` }))}
      />
    </div>
  );
}

export default memo(RobotInspector);
