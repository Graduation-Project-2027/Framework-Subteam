import { useState, useEffect, useRef, memo } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUp, ArrowDown, RotateCcw, RotateCw, ChevronsUp, ChevronsDown, Check } from "lucide-react";
import { Button, Slider, Kbd, Sun } from "./ui";
import { stateMeta, taskSteps, taskTitle, taskKind } from "../lib/mission";
import { fmt, rad2deg, num } from "../lib/geometry";
import { cx } from "../lib/cx";

const PAD = [
  { k: "w", icon: ArrowUp, label: "Forward", col: 2, row: 1 },
  { k: "a", icon: RotateCcw, label: "Turn left", col: 1, row: 2 },
  { k: "s", icon: ArrowDown, label: "Reverse", col: 2, row: 2 },
  { k: "d", icon: RotateCw, label: "Turn right", col: 3, row: 2 },
];

/** On-screen hold-to-drive buttons; keyboard driving lives in useDrive (app-wide). */
function DrivePad({ drive, disabled }) {
  const { held, press, release } = drive;
  return (
    <div className="pad" role="group" aria-label="Drive">
      {PAD.map(({ k, icon: Icon, label, col, row }) => {
        const on = held.has(k) || (k === "a" && held.has("q")) || (k === "d" && held.has("e"));
        return (
          <button
            key={k}
            type="button"
            className={cx("pad__key", on && "is-held")}
            style={{ gridColumn: col, gridRow: row }}
            aria-label={`${label} (hold ${k.toUpperCase()})`}
            aria-pressed={on}
            disabled={disabled}
            onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); press(k); }}
            onPointerUp={() => release(k)}
            onPointerCancel={() => release(k)}
            onLostPointerCapture={() => release(k)}
            onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); press(k); } }}
            onKeyUp={(e) => { if (e.key === " " || e.key === "Enter") release(k); }}
            onContextMenu={(e) => e.preventDefault()}
          >
            <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
            <Kbd>{k.toUpperCase()}</Kbd>
          </button>
        );
      })}
    </div>
  );
}

function Steps({ task, step }) {
  const steps = taskSteps(task);
  const idx = steps.findIndex((s) => s.id === step);
  return (
    <ol className="steps">
      {steps.map((s, i) => {
        const st = idx < 0 ? "todo" : i < idx ? "done" : i === idx ? "now" : "todo";
        return (
          <li key={s.id} className={cx("step", `step--${st}`)} aria-current={st === "now" ? "step" : undefined}>
            <span className="step__mark">
              {st === "done" ? <Check size={12} strokeWidth={2.5} aria-hidden="true" /> : st === "now" ? <Sun live size={8} /> : <span className="num">{i + 1}</span>}
            </span>
            <span className="step__label">{s.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

function RobotPanel({ missionState, sendCommand, disabled, drive }) {
  const meta = stateMeta(missionState.state);
  const task = missionState.current_task;
  const pose = missionState.robot_pose;
  const [speed, setSpeed] = useState(num(missionState.speed, 0.4));
  const editingSpeed = useRef(false);

  useEffect(() => {
    if (!editingSpeed.current) setSpeed(num(missionState.speed, 0.4));
  }, [missionState.speed]);

  return (
    <div className="panel panel--robot">
      <section className={cx("status", `tone--${meta.tone}`)} aria-label="Mission status">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={disabled ? "none" : missionState.state}
            className="status__head"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.16 }}
          >
            <span className="status__state">
              {meta.tone === "live" && !disabled && <Sun live size={12} />}
              {disabled ? "No robot connected" : meta.label}
            </span>
            <span className="status__hint">{disabled ? "Live data appears once rosbridge connects." : meta.hint}</span>
          </motion.div>
        </AnimatePresence>
        {!disabled && missionState.fleet?.held && (
          <p className="holdnote">
            Held by fleet traffic{missionState.fleet.by ? <> for <b>{missionState.fleet.by}</b></> : null}
            {missionState.fleet.reason ? ` (${missionState.fleet.reason})` : ""}. It continues the same task once the way is clear.
          </p>
        )}

        {task && !disabled ? (
          <div className="task-now">
            <div className="task-now__head">
              <span className="task-now__title">{taskTitle(task)}</span>
              <span className="task-now__kind">{taskKind(task)}</span>
            </div>
            <Steps task={task} step={missionState.current_step} />
            <div className="meter meter--labelled">
              <div className="meter__bar" role="progressbar" aria-label="Current step progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((missionState.progress || 0) * 100)}>
                <i style={{ transform: `scaleX(${Math.max(0.02, missionState.progress || 0)})` }} />
              </div>
              <span className="num">{Math.round((missionState.progress || 0) * 100)}%</span>
            </div>
          </div>
        ) : null}
      </section>

      <section aria-label="Telemetry">
        <div className="section-head"><h2>Telemetry</h2></div>
        <dl className="telemetry">
          <div><dt>Position</dt><dd className="num">{disabled ? "—" : <>{fmt(pose.x)}, {fmt(pose.y)}<small> m</small></>}</dd></div>
          <div><dt>Heading</dt><dd className="num">{disabled ? "—" : <>{fmt(rad2deg(num(pose.yaw)), 1)}<small>°</small></>}</dd></div>
          <div><dt>Payload</dt><dd>{disabled ? "—" : missionState.carrying ? <span className="payload is-loaded">Carrying rack</span> : "Empty"}</dd></div>
          <div><dt>Queue</dt><dd className="num">{disabled ? "—" : `${missionState.queue.length} task${missionState.queue.length === 1 ? "" : "s"}`}</dd></div>
        </dl>
      </section>

      <section aria-label="Manual control">
        <div className="section-head">
          <h2>Manual drive</h2>
          <span className="count">Hold W A S D</span>
        </div>
        <DrivePad drive={drive} disabled={disabled} />
        <div className="lift">
          <Button size="sm" icon={ChevronsUp} disabled={disabled} onClick={() => sendCommand({ action: "lift", height: 1.0 })}>Lift up</Button>
          <Button size="sm" icon={ChevronsDown} disabled={disabled} onClick={() => sendCommand({ action: "lift", height: 0.0 })}>Lift down</Button>
        </div>
        <div className="speed">
          <div className="speed__head">
            <span id="speed-label">Max speed</span>
            <span className="num speed__value">{speed.toFixed(2)}<small> m/s</small></span>
          </div>
          <Slider
            aria-label="Max speed"
            min={0.1} max={1} step={0.05}
            value={speed}
            onValueChange={(v) => { editingSpeed.current = true; setSpeed(v); }}
            onValueCommit={(v) => { editingSpeed.current = false; if (!disabled) sendCommand({ action: "set_speed", speed: v }); }}
          />
          <div className="speed__scale num" aria-hidden="true"><span>0.10</span><span>1.00</span></div>
        </div>
      </section>
    </div>
  );
}

export default memo(RobotPanel);
