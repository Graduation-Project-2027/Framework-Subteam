/**
 * Set up a fresh Raspberry Pi as a fleet robot, from the GUI, over SSH
 * (fleet_server.py + provision.py on the laptop). Connect → configure →
 * one job that names it, installs ROS 2 Jazzy, uploads and builds the
 * workspace, sets the Robot ID and starts the launcher at boot. The same
 * sheet shows a deploy job for a robot that is already set up.
 */
import { useEffect, useRef, useState } from "react";
import { Dialog } from "radix-ui";
import { X, PlugZap, Cpu, Check, CircleAlert, LoaderCircle, Minus } from "lucide-react";
import { Button, IconButton, Sun } from "../ui";
import { ROBOT_ID_RE, ROBOT_ID_HINT } from "../../lib/fleetClient";
import { nextFree } from "../../lib/fleetStatus";
import { useToast } from "../../lib/toast";
import { cx } from "../../lib/cx";

const HOST_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

function StepList({ steps }) {
  return (
    <ol className="steps jobsteps">
      {steps.map((s, i) => (
        <li key={s.id} className={cx("step", `step--${s.state === "now" ? "now" : s.state === "done" ? "done" : "todo"}`, s.state === "failed" && "is-failed")}
          aria-current={s.state === "now" ? "step" : undefined}>
          <span className="step__mark">
            {s.state === "done" ? <Check size={12} strokeWidth={2.5} aria-hidden="true" />
              : s.state === "now" ? <Sun live size={8} />
                : s.state === "failed" ? <CircleAlert size={13} strokeWidth={2.25} aria-hidden="true" />
                  : s.state === "skipped" ? <Minus size={12} strokeWidth={2.5} aria-hidden="true" />
                    : <span className="num">{i + 1}</span>}
          </span>
          <span className="step__label">{s.label}{s.state === "skipped" && <span className="muted"> · skipped</span>}</span>
        </li>
      ))}
    </ol>
  );
}

function JobLog({ lines }) {
  const ref = useRef(null);
  const pinned = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);
  return (
    <div className="stacklog joblog" ref={ref} role="log" aria-label="Setup output" tabIndex={0}
      onScroll={(e) => { const el = e.currentTarget; pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}>
      {lines.length === 0 ? <p className="stacklog__empty">Output from the Pi appears here.</p> : lines.map((l) => (
        <div key={l.seq} className={cx("stacklog__line", /✗|error|failed|E: /i.test(l.text) && "is-error", l.source === "step" && "is-step")}>
          <span className="stacklog__src">{l.source}</span>
          <code>{l.text}</code>
        </div>
      ))}
    </div>
  );
}

/** Follows a job on the laptop: steps, log, result. */
function useJob(fleet, jobId) {
  const [job, setJob] = useState(null);
  const [lines, setLines] = useState([]);
  useEffect(() => {
    if (!jobId) { setJob(null); setLines([]); return undefined; }
    let alive = true, after = 0, timer = null;
    setLines([]);
    const poll = async () => {
      try {
        const j = await fleet.client.job(jobId, after);
        if (!alive) return;
        after = j.last;
        if (j.lines.length) setLines((l) => [...l, ...j.lines].slice(-3000));
        setJob(j);
        if (j.state === "running") timer = setTimeout(poll, 800);
      } catch {
        if (alive) timer = setTimeout(poll, 2000);
      }
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [fleet.client, jobId]);
  return { job, lines };
}

export default function ProvisionSheet({ open, onOpenChange, fleet, jobId, onJob, onSelect }) {
  const toast = useToast();
  const robots = fleet.state.robots;
  const [stage, setStage] = useState("connect");
  const [conn, setConn] = useState({ host: "", user: "ubuntu", password: "" });
  const [facts, setFacts] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const { job, lines } = useJob(fleet, jobId);

  // A job in progress is what the sheet shows.
  useEffect(() => { if (jobId) setStage("job"); }, [jobId]);
  useEffect(() => {
    if (job?.state === "done" && job.result?.robot_id) {
      toast({ title: `${job.result.robot_id} is in the fleet`, body: `Hostname ${job.result.hostname} (${job.result.hostname}.local).` });
    }
  }, [job?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  const reset = () => { setStage("connect"); setFacts(null); setCfg(null); setError(null); onJob(null); };

  const connect = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fleet.client.provisionConnect(conn);
      const id = nextFree(robots, "id", "AMR-");
      setFacts(res.facts);
      setCfg({ robot_id: id, hostname: id.toLowerCase(), hostTouched: false, name: "",
        domain_id: nextFree(robots, "domain_id", ""), new_password: "", confirm: "",
        install_deps: res.facts.ws !== "yes" });
      setStage("configure");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const idOk = cfg && ROBOT_ID_RE.test(cfg.robot_id.trim());
  const clash = cfg && robots.find((r) => r.id && r.id.toLowerCase() === cfg.robot_id.trim().toLowerCase());
  const hostOk = cfg && HOST_RE.test(cfg.hostname);
  const pwOk = cfg && (!cfg.new_password || (cfg.new_password.length >= 8 && cfg.new_password === cfg.confirm));
  const noble = facts?.codename === "noble";
  const canRun = cfg && idOk && !clash && hostOk && pwOk && (noble || facts?.ros === "yes") && facts?.sudo === "yes";

  const run = async (e) => {
    e.preventDefault();
    if (!canRun) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fleet.client.provisionRun({
        ...conn, robot_id: cfg.robot_id.trim(), hostname: cfg.hostname, name: cfg.name.trim(),
        domain_id: cfg.domain_id === "" ? null : Number(cfg.domain_id),
        new_password: cfg.new_password || null, install_deps: cfg.install_deps,
      });
      setConn((c) => ({ ...c, password: "" }));
      onJob(res.job);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const set = (k) => (e) => setCfg((c) => {
    const v = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    const next = { ...c, [k]: v };
    if (k === "robot_id" && !c.hostTouched) next.hostname = String(v).trim().toLowerCase().replace(/[^a-z0-9-]/g, "-");
    if (k === "hostname") next.hostTouched = true;
    return next;
  });

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content className="sheetpanel provision" aria-describedby="prov-desc">
          <header className="sheetpanel__head">
            <Dialog.Title className="sheetpanel__title">{stage === "job" && job ? job.title : "Set up a Raspberry Pi"}</Dialog.Title>
            <Dialog.Close asChild><IconButton label="Close" icon={X} variant="ghost" /></Dialog.Close>
          </header>
          <div className="sheetpanel__body">
            <p id="prov-desc" className="hint">
              {stage === "job"
                ? "The job runs on the laptop: you can close this and come back. A first setup takes 30–60 minutes on a Pi (ROS, dependencies, build)."
                : "From a fresh Ubuntu 24.04 Pi on the same network to a fleet robot: the laptop connects over SSH, names it after its Robot ID, installs ROS 2 Jazzy, uploads and builds this workspace and starts the launcher at boot."}
            </p>

            {stage === "connect" && (
              <form className="form" onSubmit={connect}>
                <label className="field">
                  <span className="field__label">Pi address</span>
                  <input className="input" placeholder="192.168.1.40 or ubuntu.local" value={conn.host} autoFocus spellCheck={false}
                    onChange={(e) => setConn({ ...conn, host: e.target.value })} />
                </label>
                <div className="form__pair">
                  <label className="field">
                    <span className="field__label">User</span>
                    <input className="input" value={conn.user} spellCheck={false} autoComplete="username"
                      onChange={(e) => setConn({ ...conn, user: e.target.value })} />
                  </label>
                  <label className="field">
                    <span className="field__label">Password</span>
                    <input className="input" type="password" value={conn.password} autoComplete="current-password"
                      onChange={(e) => setConn({ ...conn, password: e.target.value })} />
                  </label>
                </div>
                <span className="hint">Only kept in the laptop’s memory while the setup runs; never saved.</span>
                {error && <p className="notice-inline"><CircleAlert size={14} strokeWidth={2} aria-hidden="true" />{error}</p>}
                <div className="form__actions">
                  <Button variant="primary" icon={PlugZap} type="submit" disabled={busy || !conn.host.trim() || !conn.password}>
                    {busy ? "Connecting…" : "Connect"}
                  </Button>
                </div>
              </form>
            )}

            {stage === "configure" && facts && cfg && (
              <form className="form" onSubmit={run}>
                <section className="pifacts" aria-label="This Pi">
                  <Cpu size={18} strokeWidth={1.75} aria-hidden="true" />
                  <dl className="kv">
                    <div><dt>Hostname now</dt><dd>{facts.host}</dd></div>
                    <div><dt>System</dt><dd>{facts.os}</dd></div>
                    <div><dt>Board</dt><dd className="num">{facts.arch} · {facts.mem ? `${(facts.mem / 1024).toFixed(1)} GB` : "?"}</dd></div>
                    <div><dt>ROS 2 Jazzy</dt><dd>{facts.ros === "yes" ? "Installed" : "Will be installed"}</dd></div>
                  </dl>
                </section>
                {!noble && facts.ros !== "yes" && <p className="notice-inline"><CircleAlert size={14} aria-hidden="true" />ROS 2 Jazzy needs Ubuntu 24.04. Flash Ubuntu Server 24.04 on this Pi first.</p>}
                {facts.sudo !== "yes" && <p className="notice-inline"><CircleAlert size={14} aria-hidden="true" />{conn.user} can’t use sudo with this password.</p>}

                <div className="form__pair">
                  <label className="field">
                    <span className="field__label">Robot ID</span>
                    <input className="input" value={cfg.robot_id} onChange={set("robot_id")} spellCheck={false} aria-invalid={!idOk || !!clash} />
                  </label>
                  <label className="field">
                    <span className="field__label">Hostname</span>
                    <input className="input" value={cfg.hostname} onChange={set("hostname")} spellCheck={false} aria-invalid={!hostOk} />
                  </label>
                </div>
                {!idOk && <span className="field__error">{ROBOT_ID_HINT}</span>}
                {clash && <span className="field__error">{clash.label} already uses this ID.</span>}
                {!hostOk && <span className="field__error">Lower-case letters, digits and “-” only.</span>}
                <span className="hint">The Pi will answer to <b>{cfg.hostname || "…"}</b> and <b>{cfg.hostname || "…"}.local</b>, so it’s easy to find among the network’s devices.</span>

                <div className="form__pair">
                  <label className="field">
                    <span className="field__label">Name <span className="field__opt">optional</span></span>
                    <input className="input" value={cfg.name} maxLength={24} onChange={set("name")} />
                  </label>
                  <label className="field">
                    <span className="field__label">ROS domain</span>
                    <input className="input num" inputMode="numeric" value={cfg.domain_id} onChange={(e) => setCfg((c) => ({ ...c, domain_id: e.target.value.replace(/\D/g, "").slice(0, 3) }))} />
                  </label>
                </div>

                <div className="form__pair">
                  <label className="field">
                    <span className="field__label">New password <span className="field__opt">optional</span></span>
                    <input className="input" type="password" value={cfg.new_password} onChange={set("new_password")} autoComplete="new-password" />
                  </label>
                  <label className="field">
                    <span className="field__label">Repeat it</span>
                    <input className="input" type="password" value={cfg.confirm} onChange={set("confirm")} autoComplete="new-password" disabled={!cfg.new_password} />
                  </label>
                </div>
                {!pwOk && <span className="field__error">{cfg.new_password.length < 8 ? "At least 8 characters." : "The two passwords differ."}</span>}

                <label className="checkrow">
                  <input type="checkbox" checked={cfg.install_deps} onChange={set("install_deps")} />
                  <span>Install the robot’s dependencies (<code>install_dependencies.sh</code>). Needed on a fresh Pi.</span>
                </label>
                {error && <p className="notice-inline"><CircleAlert size={14} strokeWidth={2} aria-hidden="true" />{error}</p>}
                <div className="form__actions">
                  <Button variant="ghost" onClick={reset}>Back</Button>
                  <Button variant="primary" type="submit" disabled={!canRun || busy}>{busy ? "Starting…" : `Set up ${cfg.robot_id.trim() || "robot"}`}</Button>
                </div>
              </form>
            )}

            {stage === "job" && (
              job ? (
                <>
                  <StepList steps={job.steps} />
                  {job.state === "failed" && <p className="notice-inline"><CircleAlert size={14} strokeWidth={2} aria-hidden="true" />{job.error}</p>}
                  {job.state === "running" && <p className="hint jobnote"><LoaderCircle size={14} className="spin" aria-hidden="true" /> Working… the robot keeps going even if this sheet is closed.</p>}
                  <JobLog lines={lines} />
                  <div className="form__actions">
                    {job.state !== "running" && <Button variant="ghost" onClick={reset}>Set up another Pi</Button>}
                    {job.state === "done" && job.result?.key && (
                      <Button variant="primary" onClick={() => { onSelect(job.result.key); onOpenChange(false); }}>Show {job.result.robot_id || "robot"}</Button>
                    )}
                  </div>
                </>
              ) : <p className="hint">Loading the job…</p>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
