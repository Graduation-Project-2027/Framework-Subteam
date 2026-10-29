import { useState, memo } from "react";
import { Dialog } from "radix-ui";
import { Plus, Wifi, X, CircleAlert, TriangleAlert, Cpu } from "lucide-react";
import { Button, IconButton } from "../ui";
import { RobotGlyph } from "./common";
import { robotLine, robotState, robotTone, nextFree } from "../../lib/fleetStatus";
import { ROBOT_ID_RE, ROBOT_ID_HINT } from "../../lib/fleetClient";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

/** First-time setup: give a new robot its Robot ID (and domain), then add it. */
export function SetupDialog({ robot, robots, open, onOpenChange, fleet }) {
  const toast = useToast();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [seeded, setSeeded] = useState(null);
  if (open && robot && seeded !== robot.key) {
    setSeeded(robot.key);
    setId(robot.id || nextFree(robots, "id", "AMR-"));
    setName(robot.name || "");
    setDomain(robot.settings?.domain_id ?? nextFree(robots, "domain_id", ""));
    setError(null);
  }
  const clash = robots.find((r) => r.key !== robot?.key && r.id && r.id.toLowerCase() === id.trim().toLowerCase());
  const domainClash = domain !== "" && robots.find((r) => r.key !== robot?.key && r.adopted && String(r.settings?.domain_id) === String(domain));
  const valid = ROBOT_ID_RE.test(id.trim()) && !clash;

  const save = async (e) => {
    e.preventDefault();
    if (!valid || !robot) return;
    setBusy(true);
    setError(null);
    try {
      await fleet.act((c) => c.settings(robot.key, { id: id.trim(), name: name.trim(), domain_id: domain === "" ? null : Number(domain) }));
      await fleet.act((c) => c.adopt(robot.key));
      toast({ title: `${id.trim()} joined the fleet` });
      onOpenChange(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog__overlay" />
        <Dialog.Content className="dialog dialog--form" aria-describedby="setup-desc">
          <Dialog.Title className="dialog__title">Set up {robot?.hostname || "new robot"}</Dialog.Title>
          <p id="setup-desc" className="dialog__body">
            Saved on the robot in <code>~/.ros/amr_robot.json</code>, outside the workspace, so every robot can run the same copy of the repo.
          </p>
          <form className="form" onSubmit={save}>
            <label className="field">
              <span className="field__label">Robot ID</span>
              <input className="input" value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" spellCheck={false}
                aria-invalid={!valid} autoFocus />
              {!ROBOT_ID_RE.test(id.trim()) && <span className="field__error">{ROBOT_ID_HINT}</span>}
              {clash && <span className="field__error">{clash.label} already uses this ID.</span>}
            </label>
            <label className="field">
              <span className="field__label">Name <span className="field__opt">optional</span></span>
              <input className="input" value={name} maxLength={24} onChange={(e) => setName(e.target.value)} autoComplete="off" />
            </label>
            <label className="field">
              <span className="field__label">ROS domain</span>
              <input className="input num" inputMode="numeric" value={domain} onChange={(e) => setDomain(e.target.value.replace(/\D/g, "").slice(0, 3))} />
              <span className={cx("hint", domainClash && "hint--warn")}>
                {domainClash ? `${domainClash.label} already uses domain ${domain}; their topics would mix.` : "One domain per robot keeps their ROS graphs apart on shared Wi-Fi. Applies when the stack next starts."}
              </span>
            </label>
            {error && <p className="notice-inline"><CircleAlert size={14} strokeWidth={2} aria-hidden="true" />{error}</p>}
            <div className="dialog__actions">
              <Dialog.Close asChild><Button variant="secondary">Cancel</Button></Dialog.Close>
              <Button variant="primary" type="submit" disabled={!valid || busy}>{busy ? "Saving…" : "Save and add to fleet"}</Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function AddByAddress({ fleet }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [host, setHost] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  if (!open) {
    return (
      <button type="button" className="linkbtn" onClick={() => setOpen(true)}>
        Robot not showing up? Add it by address
      </button>
    );
  }
  const submit = async (e) => {
    e.preventDefault();
    if (!host.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fleet.act((c) => c.add(host));
      toast({ title: `Found ${res.robot.label} at ${res.robot.host}` });
      setHost("");
      setOpen(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="addhost" onSubmit={submit}>
      <label className="field">
        <span className="field__label">Robot address</span>
        <div className="addhost__row">
          <input className="input" placeholder="192.168.1.31" value={host} onChange={(e) => setHost(e.target.value)} autoFocus spellCheck={false} />
          <Button variant="primary" type="submit" disabled={busy || !host.trim()}>{busy ? "Asking…" : "Add"}</Button>
          <IconButton label="Cancel" icon={X} variant="ghost" onClick={() => { setOpen(false); setError(null); }} />
        </div>
      </label>
      <span className="hint">For networks that block broadcasts. The robot must run <code>launcher_server.py</code>.</span>
      {error && <span className="field__error">{error}</span>}
    </form>
  );
}

function Row({ robot, selected, onSelect, warn }) {
  const tone = robotTone(robot);
  return (
    <li>
      <button
        type="button"
        className={cx("rrow", selected && "is-selected")}
        onClick={() => onSelect(robot.key)}
        aria-current={selected ? "true" : undefined}
      >
        <RobotGlyph robot={robot} />
        <span className="rrow__body">
          <span className="rrow__title">
            <b>{robot.label}</b>
            {robot.name && <span className="rrow__name">{robot.name}</span>}
          </span>
          <span className="rrow__sub">{robotLine(robot)}</span>
        </span>
        <span className={cx("rrow__state", `tone--${tone}`)}>
          {warn && <TriangleAlert size={13} strokeWidth={2} aria-label="Has a warning" />}
          {robotState(robot)}
        </span>
      </button>
    </li>
  );
}

function Roster({ fleet, selected, onSelect, onProvision, provisioning }) {
  const [setupKey, setSetupKey] = useState(null);
  const toast = useToast();
  const { robots, warnings } = fleet.state;
  const inFleet = robots.filter((r) => r.adopted);
  const found = robots.filter((r) => !r.adopted && r.status === "online");
  const warned = new Set(warnings.flatMap((w) => w.robots || []));
  const setupRobot = robots.find((r) => r.key === setupKey);

  const adopt = (r) => fleet.act((c) => c.adopt(r.key))
    .then(() => toast({ title: `${r.label} joined the fleet` }))
    .catch((e) => toast({ title: "Couldn't add the robot", body: e.message, tone: "error" }));

  return (
    <div className="panel roster">
      <section aria-labelledby="fleet-h">
        <div className="section-head">
          <h2 id="fleet-h">In the fleet</h2>
          <span className="count num">{inFleet.length}</span>
        </div>
        {inFleet.length ? (
          <ul className="rlist">
            {inFleet.map((r) => <Row key={r.key} robot={r} selected={r.key === selected} onSelect={onSelect} warn={warned.has(r.key)} />)}
          </ul>
        ) : (
          <p className="empty">
            {fleet.status === "online"
              ? "No robots yet. Robots running launcher_server.py on this network show up below within a second."
              : "Waiting for the fleet manager."}
          </p>
        )}
      </section>

      <section aria-labelledby="found-h">
        <div className="section-head">
          <h2 id="found-h"><Wifi size={14} strokeWidth={1.75} aria-hidden="true" /> Found on the network</h2>
          <span className="count num">{found.length}</span>
        </div>
        {found.length ? (
          <ul className="rlist">
            {found.map((r) => (
              <li key={r.key} className="found">
                <RobotGlyph robot={r} />
                <span className="rrow__body">
                  <span className="rrow__title"><b>{r.configured ? r.label : r.hostname || "New robot"}</b></span>
                  <span className="rrow__sub num">{r.configured ? `${r.hostname || ""} · ` : "Needs a Robot ID · "}{r.host}</span>
                </span>
                {r.configured ? (
                  <Button size="sm" variant="secondary" icon={Plus} onClick={() => adopt(r)}>Add</Button>
                ) : (
                  <Button size="sm" variant="primary" onClick={() => setSetupKey(r.key)}>Set up</Button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty empty--row">Listening for robots… New robots appear here on their own.</p>
        )}
        <AddByAddress fleet={fleet} />
        {onProvision && (
          <Button className="provbtn" variant="secondary" icon={Cpu} onClick={onProvision}>
            {provisioning ? "Show the Pi setup" : "Set up a new Raspberry Pi"}
          </Button>
        )}
      </section>

      {warnings.length > 0 && (
        <section aria-labelledby="warn-h">
          <div className="section-head"><h2 id="warn-h">Needs attention</h2><span className="count num">{warnings.length}</span></div>
          <ul className="warnlist">
            {warnings.map((w) => (
              <li key={w.text} className={cx("warn", `warn--${w.tone}`)}>
                {w.tone === "fault" ? <CircleAlert size={14} strokeWidth={2} aria-hidden="true" /> : <TriangleAlert size={14} strokeWidth={2} aria-hidden="true" />}
                <span>{w.text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <SetupDialog robot={setupRobot} robots={robots} open={!!setupRobot} onOpenChange={(o) => !o && setSetupKey(null)} fleet={fleet} />
    </div>
  );
}

export default memo(Roster);
