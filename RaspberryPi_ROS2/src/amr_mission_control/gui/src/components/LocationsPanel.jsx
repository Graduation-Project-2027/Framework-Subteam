import { useState, useEffect, useMemo, memo } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Plus, Pencil, Trash2, Crosshair, MapPin, Spline, Check } from "lucide-react";
import { Button, IconButton, Segmented } from "./ui";
import { fmt, rad2deg, deg2rad } from "../lib/geometry";
import { cx } from "../lib/cx";

const ease = [0.22, 1, 0.36, 1];
const natural = (a, b) => String(a.name || a.id).localeCompare(String(b.name || b.id), undefined, { numeric: true });
// Autofocus only with a mouse/trackpad, so phones don't pop the keyboard.
const finePointer = () => window.matchMedia("(pointer: fine)").matches;

function Editor({ initial, kind, onSave, onCancel, onPick }) {
  const [form, setForm] = useState(initial);
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const numVal = (v) => (v === "" || v === "-" ? v : Number(v));
  const valid = String(form.name || "").trim() && [form.x, form.y].every((v) => v !== "" && Number.isFinite(Number(v)));

  const save = (e) => {
    e.preventDefault();
    if (!valid) return;
    onSave({
      ...form,
      name: String(form.name).trim(),
      x: Number(form.x), y: Number(form.y),
      yaw: deg2rad(Number(form.heading) || 0),
    });
  };

  return (
    <motion.form
      className="editor"
      onSubmit={save}
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      transition={{ duration: 0.2, ease }}
    >
      <div className="editor__inner">
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" name="name" value={form.name} onChange={set("name")} autoFocus={finePointer()} autoComplete="off" spellCheck={false} />
        </label>
        <div className="editor__pose">
          <label className="field"><span className="field__label">X (m)</span>
            <input className="input num" name="x" autoComplete="off" inputMode="decimal" value={form.x} onChange={(e) => setForm((f) => ({ ...f, x: numVal(e.target.value) }))} />
          </label>
          <label className="field"><span className="field__label">Y (m)</span>
            <input className="input num" name="y" autoComplete="off" inputMode="decimal" value={form.y} onChange={(e) => setForm((f) => ({ ...f, y: numVal(e.target.value) }))} />
          </label>
          <label className="field"><span className="field__label">Heading (°)</span>
            <input className="input num" name="heading" autoComplete="off" inputMode="numeric" value={form.heading} onChange={(e) => setForm((f) => ({ ...f, heading: numVal(e.target.value) }))} />
          </label>
        </div>
        <Button size="sm" variant="secondary" icon={MapPin} onClick={() => onPick((p) => setForm((f) => ({
          ...f, x: Number(p.x.toFixed(2)), y: Number(p.y.toFixed(2)), heading: Math.round(rad2deg(p.yaw)),
        })))}>
          Pick on map
        </Button>
        {kind !== "dock" && (
          <div className="field">
            <span className="field__label" id="state-label">State</span>
            <Segmented
              size="sm"
              aria-label="State"
              value={form.state}
              onValueChange={(v) => setForm((f) => ({ ...f, state: v }))}
              options={[{ value: "occupied", label: "Occupied" }, { value: "free", label: "Free" }]}
            />
          </div>
        )}
        <div className="editor__actions">
          <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
          <Button size="sm" variant="primary" type="submit" icon={Check} disabled={!valid}>Save</Button>
        </div>
      </div>
    </motion.form>
  );
}

function toForm(item, kind) {
  return {
    ...item,
    name: item?.name || "",
    x: Number(fmt(item?.x ?? 0)),
    y: Number(fmt(item?.y ?? 0)),
    heading: Math.round(rad2deg(item?.yaw || 0)),
    state: item?.state || (kind === "zone" ? "free" : "occupied"),
  };
}

function SiteRow({ item, kind, hovered, onHover, onEdit, onFlip, onFocus, onDelete, editing }) {
  const [arm, setArm] = useState(false);
  useEffect(() => {
    if (!arm) return undefined;
    const t = setTimeout(() => setArm(false), 3000);
    return () => clearTimeout(t);
  }, [arm]);
  const occupied = kind !== "dock" && (item.state || (kind === "zone" ? "free" : "occupied")) === "occupied";

  return (
    <li
      className={cx("site", hovered && "is-hovered", editing && "is-editing")}
      onPointerEnter={() => onHover(item.id)}
      onPointerLeave={() => onHover(null)}
    >
      <span className={cx("glyph", kind === "dock" ? "glyph--dock" : occupied ? "glyph--occ" : "glyph--free", kind === "zone" && "glyph--zone")} aria-hidden="true" />
      <button type="button" className="site__name" onClick={onEdit} aria-label={`Edit ${item.name || item.id}`}>
        {item.name || item.id}
      </button>
      <span className="site__xy num">{fmt(item.x, 1)}, {fmt(item.y, 1)}</span>
      {kind !== "dock" && (
        <button
          type="button"
          className={cx("state-toggle", occupied ? "is-occ" : "is-free")}
          onClick={onFlip}
          aria-label={`${item.name || item.id} is ${occupied ? "occupied" : "free"}. Mark ${occupied ? "free" : "occupied"}`}
        >
          {occupied ? "Occupied" : "Free"}
        </button>
      )}
      <span className="site__actions">
        <IconButton label="Show on map" icon={Crosshair} variant="ghost" size="sm" onClick={onFocus} />
        {kind === "dock" ? (
          <IconButton label="Edit dock" icon={Pencil} variant="ghost" size="sm" onClick={onEdit} />
        ) : arm ? (
          <Button size="sm" variant="danger-quiet" onClick={onDelete} autoFocus>Delete</Button>
        ) : (
          <IconButton label="Delete" icon={Trash2} variant="ghost" size="sm" onClick={() => setArm(true)} />
        )}
      </span>
    </li>
  );
}

function LocationsPanel({ missionState, sendCommand, onPick, onStartLane, laneActive, hoverId, onHoverId, laneHover, onLaneHover, onFocus, disabled }) {
  const loc = missionState.locations;
  const racks = useMemo(() => [...loc.racks].sort(natural), [loc.racks]);
  const zones = useMemo(() => [...loc.drop_zones].sort(natural), [loc.drop_zones]);
  const lanes = loc.lanes || [];
  // editing: { kind: "rack"|"zone"|"dock", id: string|null }
  const [editing, setEditing] = useState(null);

  const pushSites = (nextRacks, nextZones) =>
    sendCommand({ action: "update_locations", racks: nextRacks, drop_zones: nextZones });

  const save = (kind, id, data) => {
    const clean = { name: data.name, x: data.x, y: data.y, yaw: data.yaw, state: data.state };
    if (kind === "dock") {
      sendCommand({ action: "update_dock", name: data.name, x: data.x, y: data.y, yaw: data.yaw });
    } else {
      const key = kind === "rack" ? "racks" : "drop_zones";
      const list = loc[key];
      const next = id
        ? list.map((it) => (it.id === id ? { ...it, ...clean } : it))
        : [...list, { ...clean, id: `${key}_${Date.now()}` }];
      pushSites(kind === "rack" ? next : loc.racks, kind === "zone" ? next : loc.drop_zones);
    }
    setEditing(null);
  };

  const remove = (kind, id) => {
    if (kind === "rack") pushSites(loc.racks.filter((r) => r.id !== id), loc.drop_zones);
    else pushSites(loc.racks, loc.drop_zones.filter((z) => z.id !== id));
  };

  const flip = (item) => sendCommand({
    action: "set_location_state", id: item.id, state: (item.state || "occupied") === "occupied" ? "free" : "occupied",
  });

  const nextName = (prefix, list) => {
    const nums = list.map((l) => parseInt(String(l.name).replace(/\D/g, ""), 10)).filter(Number.isFinite);
    return `${prefix}${(nums.length ? Math.max(...nums) : 0) + 1}`;
  };

  const editorFor = (kind, item) => (
    <Editor
      key={`${kind}-${item?.id || "new"}`}
      kind={kind}
      initial={item ? toForm(item, kind) : toForm({ name: kind === "rack" ? nextName("R", loc.racks) : nextName("Z", loc.drop_zones), x: missionState.robot_pose.x, y: missionState.robot_pose.y }, kind)}
      onCancel={() => setEditing(null)}
      onSave={(d) => save(kind, item?.id || null, d)}
      onPick={(cb) => onPick(item?.name || (kind === "rack" ? "New rack" : kind === "zone" ? "New drop zone" : "Dock"), cb)}
    />
  );

  const group = (title, kind, items, prefix) => (
    <section className="group" aria-label={title}>
      <div className="section-head">
        <h2>{title}</h2>
        <span className="count num">{items.length}</span>
      </div>
      <AnimatePresence initial={false}>
        {editing?.kind === kind && !editing.id && editorFor(kind, null)}
      </AnimatePresence>
      {items.length === 0 && !(editing?.kind === kind && !editing.id) ? (
        <p className="empty empty--row">No {prefix} yet.</p>
      ) : (
        <ul className="sites">
          {items.map((it) => (
            <SiteFragment key={it.id}>
              <SiteRow
                item={it}
                kind={kind}
                hovered={hoverId === it.id}
                editing={editing?.id === it.id}
                onHover={onHoverId}
                onEdit={() => setEditing({ kind, id: it.id })}
                onFlip={() => flip(it)}
                onFocus={() => onFocus(it)}
                onDelete={() => remove(kind, it.id)}
              />
              <AnimatePresence initial={false}>
                {editing?.id === it.id && <li className="site-editor">{editorFor(kind, it)}</li>}
              </AnimatePresence>
            </SiteFragment>
          ))}
        </ul>
      )}
    </section>
  );

  const dockItem = { ...loc.dock, id: "dock" };

  return (
    <div className={cx("panel panel--locations", disabled && "is-disabled")}>
      <div className="toolbar" role="toolbar" aria-label="Add to floor plan">
        <Button size="sm" icon={Plus} onClick={() => setEditing({ kind: "rack", id: null })} disabled={disabled}>Rack</Button>
        <Button size="sm" icon={Plus} onClick={() => setEditing({ kind: "zone", id: null })} disabled={disabled}>Drop zone</Button>
        <Button size="sm" icon={Spline} onClick={onStartLane} disabled={disabled || laneActive} aria-pressed={laneActive}>Lane</Button>
      </div>

      <section className="group" aria-label="Dock">
        <div className="section-head"><h2>Dock</h2></div>
        <ul className="sites">
          <SiteRow
            item={dockItem}
            kind="dock"
            hovered={hoverId === "dock"}
            editing={editing?.kind === "dock"}
            onHover={onHoverId}
            onEdit={() => setEditing({ kind: "dock", id: "dock" })}
            onFocus={() => onFocus(dockItem)}
          />
          <AnimatePresence initial={false}>
            {editing?.kind === "dock" && <li className="site-editor">{editorFor("dock", dockItem)}</li>}
          </AnimatePresence>
        </ul>
      </section>

      {group("Racks", "rack", racks, "racks")}
      {group("Drop zones", "zone", zones, "drop zones")}

      <section className="group" aria-label="Lanes">
        <div className="section-head">
          <h2>Lanes</h2>
          <span className="count num">{lanes.length}</span>
        </div>
        {lanes.length === 0 ? (
          <p className="empty empty--row">No lanes. Lanes are the corridors the robot follows; draw one with <strong>Lane</strong>. Corners and junctions are rounded automatically.</p>
        ) : (
          <ul className="sites">
            {lanes.map((lane) => (
              <LaneRow
                key={lane.id}
                lane={lane}
                hovered={laneHover === lane.id}
                onHover={onLaneHover}
                onDelete={() => sendCommand({ action: "update_lanes", lanes: lanes.filter((l) => l.id !== lane.id) })}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function LaneRow({ lane, onDelete, onHover, hovered }) {
  const [arm, setArm] = useState(false);
  useEffect(() => {
    if (!arm) return undefined;
    const t = setTimeout(() => setArm(false), 3000);
    return () => clearTimeout(t);
  }, [arm]);
  const pts = lane.points || [];
  let len = 0;
  for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
  return (
    <li className={cx("site", hovered && "is-hovered")} onPointerEnter={() => onHover(lane.id)} onPointerLeave={() => onHover(null)}>
      <span className="glyph glyph--lane" aria-hidden="true" />
      <span className="site__name site__name--static">{lane.name || lane.id}</span>
      <span className="site__xy num">{len.toFixed(1)} m</span>
      <span className="site__actions">
        {arm
          ? <Button size="sm" variant="danger-quiet" onClick={onDelete} autoFocus>Delete</Button>
          : <IconButton label="Delete lane" icon={Trash2} variant="ghost" size="sm" onClick={() => setArm(true)} />}
      </span>
    </li>
  );
}

const SiteFragment = ({ children }) => children;

export default memo(LocationsPanel);
