import { useState, useEffect, useMemo, useRef, memo } from "react";
import { Reorder, AnimatePresence, motion, useDragControls } from "motion/react";
import { GripVertical, X, Plus, ArrowRight, Repeat2, Navigation } from "lucide-react";
import { Button, IconButton, Select, Segmented, Sun } from "./ui";
import { allSites, siteState, taskSteps, taskTitle, taskKind } from "../lib/mission";
import { cx } from "../lib/cx";

const ease = [0.22, 1, 0.36, 1];

function Composer({ locations, sendCommand, disabled, mapping }) {
  const [type, setType] = useState("transport");
  const sites = useMemo(() => allSites(locations), [locations]);
  const sources = sites.filter((s) => siteState(s) === "occupied");
  const targets = sites.filter((s) => siteState(s) === "free");
  const dock = locations?.dock;
  const gotoOptions = [
    ...(dock ? [{ value: "dock", label: dock.name || "Dock" }] : []),
    ...sites.map((s) => ({ value: s.id, label: s.name || s.id, hint: s.kind === "zone" ? "Drop zone" : "Rack" })),
  ];

  const [fromId, setFromId] = useState("");
  const [toId, setToId] = useState("");
  const [gotoId, setGotoId] = useState("");

  // keep selections valid as occupancy changes
  const validFrom = sources.some((s) => s.id === fromId) ? fromId : sources[0]?.id || "";
  const validTo = targets.some((s) => s.id === toId && s.id !== validFrom) ? toId : targets.find((t) => t.id !== validFrom)?.id || "";
  const validGoto = gotoOptions.some((o) => o.value === gotoId) ? gotoId : gotoOptions[0]?.value || "";

  const add = () => {
    if (type === "transport") {
      const from = sites.find((s) => s.id === validFrom);
      const to = sites.find((s) => s.id === validTo);
      if (!from || !to) return;
      sendCommand({
        action: "add_task", type: "transport", rack_id: from.id,
        from_name: from.name || from.id, to_name: to.name || to.id,
        from: { id: from.id, name: from.name, x: from.x, y: from.y, yaw: from.yaw || 0 },
        to: { id: to.id, name: to.name, x: to.x, y: to.y, yaw: to.yaw || 0 },
      });
    } else {
      const to = validGoto === "dock" ? { ...dock, id: "dock" } : sites.find((s) => s.id === validGoto);
      if (!to) return;
      sendCommand({
        action: "add_task", type: "navigate", from_name: "",
        to_name: to.name || to.id, to: { x: to.x, y: to.y, yaw: to.yaw || 0 },
      });
    }
  };

  let blocker = null;
  if (disabled) blocker = "Racks and drop zones load from the robot once it connects.";
  else if (mapping) blocker = "Mapping mode has no navigation. Save the map, then navigate with it to run tasks.";
  else if (type === "transport") {
    if (!sites.length) blocker = "No racks yet. Add them under Locations.";
    else if (!sources.length) blocker = "Every rack is free, so there is nothing to pick up.";
    else if (!targets.filter((t) => t.id !== validFrom).length) blocker = "No free destination. Right-click a rack on the map to mark it free, or add a drop zone.";
  }
  const canAdd = !disabled && !blocker && (type === "transport" ? validFrom && validTo : validGoto);

  return (
    <form className="composer" onSubmit={(e) => { e.preventDefault(); if (canAdd) add(); }}>
      <Segmented
        aria-label="Task type"
        value={type}
        onValueChange={setType}
        options={[
          { value: "transport", label: "Move rack", icon: Repeat2 },
          { value: "navigate", label: "Go to", icon: Navigation },
        ]}
      />
      {type === "transport" ? (
        <div className="composer__route">
          <label className="field">
            <span className="field__label">From</span>
            <Select aria-label="Pick up from" value={validFrom} onValueChange={setFromId} placeholder="No occupied rack"
              options={sources.map((s) => ({ value: s.id, label: s.name || s.id }))} disabled={!sources.length} />
          </label>
          <ArrowRight className="composer__arrow" size={16} strokeWidth={1.75} aria-hidden="true" />
          <label className="field">
            <span className="field__label">To</span>
            <Select aria-label="Place at" value={validTo} onValueChange={setToId} placeholder="No free slot"
              options={targets.filter((t) => t.id !== validFrom).map((s) => ({ value: s.id, label: s.name || s.id }))}
              disabled={!targets.length} />
          </label>
        </div>
      ) : (
        <label className="field">
          <span className="field__label">Destination</span>
          <Select aria-label="Destination" value={validGoto} onValueChange={setGotoId} options={gotoOptions} />
        </label>
      )}
      {blocker && <p className="composer__note">{blocker}</p>}
      <Button type="submit" variant="primary" icon={Plus} disabled={!canAdd} className="composer__submit">
        Add to queue
      </Button>
    </form>
  );
}

function QueueItem({ task, index, active, step, progress, onRemove, dragDisabled, onDragEnd }) {
  const controls = useDragControls();
  const [arm, setArm] = useState(false);
  useEffect(() => {
    if (!arm) return undefined;
    const t = setTimeout(() => setArm(false), 3000);
    return () => clearTimeout(t);
  }, [arm]);
  const steps = taskSteps(task);
  const stepLabel = steps.find((s) => s.id === step)?.label;
  return (
    <Reorder.Item
      value={task.id}
      dragListener={false}
      dragControls={controls}
      onDragEnd={onDragEnd}
      className={cx("qitem", active && "qitem--active")}
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.18, ease }}
      layout="position"
    >
      <div className="qitem__row">
        <button
          type="button"
          className="qitem__grip"
          aria-label={`Reorder ${taskTitle(task)}`}
          disabled={dragDisabled}
          onPointerDown={(e) => { if (!dragDisabled) controls.start(e); }}
        >
          <GripVertical size={16} strokeWidth={1.75} aria-hidden="true" />
        </button>
        <span className="qitem__n">{active ? <Sun live size={9} /> : index + 1}</span>
        <div className="qitem__body">
          <span className="qitem__title">{taskTitle(task)}</span>
          <span className="qitem__sub">{active && stepLabel ? stepLabel : taskKind(task)}</span>
        </div>
        {arm ? (
          <Button size="sm" variant="danger-quiet" className="qitem__confirm" onClick={() => onRemove(task.id)} autoFocus>
            Remove
          </Button>
        ) : (
          <IconButton label="Remove task" icon={X} variant="ghost" size="sm" className="qitem__remove" onClick={() => setArm(true)} />
        )}
      </div>
      {active && (
        <div className="meter" role="progressbar" aria-label="Step progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(progress * 100)}>
          <i style={{ transform: `scaleX(${Math.max(0.02, progress)})` }} />
        </div>
      )}
    </Reorder.Item>
  );
}

function TasksPanel({ missionState, sendCommand, disabled, mapping }) {
  const queue = missionState.queue;
  const currentId = missionState.current_task?.id;
  const [order, setOrderState] = useState(() => queue.map((t) => t.id));
  const orderRef = useRef(order);
  const setOrder = (o) => { orderRef.current = o; setOrderState(o); };
  const dragging = useRef(false);
  const byId = useMemo(() => Object.fromEntries(queue.map((t) => [t.id, t])), [queue]);

  useEffect(() => {
    if (!dragging.current) {
      const ids = queue.map((t) => t.id);
      orderRef.current = ids;
      setOrderState(ids);
    }
  }, [queue]);

  const commit = () => {
    dragging.current = false;
    const next = orderRef.current;
    if (next.join() !== queue.map((t) => t.id).join()) sendCommand({ action: "reorder_tasks", order: next });
  };

  const visible = order.filter((id) => byId[id]);
  const waiting = queue.filter((t) => t.id !== currentId).length;

  return (
    <div className="panel panel--tasks">
      <Composer locations={missionState.locations} sendCommand={sendCommand} disabled={disabled} mapping={mapping} />
      <div className="section-head">
        <h2>Queue</h2>
        <span className="count">{queue.length ? `${waiting} waiting${currentId ? ", 1 running" : ""}` : "Empty"}</span>
      </div>
      {visible.length === 0 ? (
        <p className="empty">
          Add a task above, or double-click the map to send the robot straight to a point.
        </p>
      ) : (
        <Reorder.Group
          axis="y"
          values={visible}
          onReorder={(o) => { dragging.current = true; setOrder(o); }}
          className="queue"
          as="ol"
        >
          <AnimatePresence initial={false}>
            {visible.map((id, i) => (
              <QueueItem
                key={id}
                task={byId[id]}
                index={i}
                active={id === currentId}
                step={missionState.current_step}
                progress={missionState.progress || 0}
                dragDisabled={id === currentId || disabled}
                onDragEnd={commit}
                onRemove={(tid) => sendCommand({ action: "remove_task", id: tid })}
              />
            ))}
          </AnimatePresence>
        </Reorder.Group>
      )}
      <AnimatePresence>
        {missionState.state === "IDLE" && queue.length > 0 && !disabled && (
          <motion.p className="hint" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.15 }}>
            Press <strong>Start mission</strong> to run the queue from the top.
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

export default memo(TasksPanel);
