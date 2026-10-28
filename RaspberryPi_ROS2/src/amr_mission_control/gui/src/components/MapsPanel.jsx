import { useEffect, useRef, useState, memo } from "react";
import { MoreHorizontal, Navigation, Pencil, Copy, Star, Trash2, RotateCcw, Check, X, Radar, TextCursorInput } from "lucide-react";
import { Button, IconButton, Menu, MenuTrigger, MenuContent, MenuItem, MenuSeparator, ConfirmDialog } from "./ui";
import { loadMapGrid } from "../hooks/useStack";
import { knownBounds } from "../lib/grid";
import { rasterize } from "./map/draw";
import { MAP_NAME_RE, MAP_NAME_HINT } from "../lib/stackClient";
import { describeStack, ago } from "../lib/stackPrefs";
import { useToast } from "../lib/toast";
import { cx } from "../lib/cx";

function Thumb({ client, info }) {
  const ref = useRef(null);
  useEffect(() => {
    let alive = true;
    if (!info.has_image) return undefined;
    loadMapGrid(client, info).then((g) => {
      const c = ref.current;
      if (!alive || !c) return;
      const ctx = c.getContext("2d");
      const b = knownBounds(g) || { x0: g.origin.x, y0: g.origin.y, x1: g.origin.x + g.width * g.resolution, y1: g.origin.y + g.height * g.resolution };
      const r = g.resolution;
      const sx = (b.x0 - g.origin.x) / r, sw = (b.x1 - b.x0) / r;
      const sy = g.height - (b.y1 - g.origin.y) / r, sh = (b.y1 - b.y0) / r;
      const k = Math.min(c.width / sw, c.height / sh);
      ctx.fillStyle = "#e8e9ec";
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(rasterize(g, "map"), sx, sy, sw, sh, (c.width - sw * k) / 2, (c.height - sh * k) / 2, sw * k, sh * k);
    }).catch(() => {});
    return () => { alive = false; };
  }, [client, info]);
  return <canvas ref={ref} className="thumb" width="128" height="96" aria-hidden="true" />;
}

function NameForm({ initial, label, onSubmit, onCancel, busy }) {
  const [value, setValue] = useState(initial);
  const valid = MAP_NAME_RE.test(value);
  return (
    <form className="mapname" onSubmit={(e) => { e.preventDefault(); if (valid) onSubmit(value); }}>
      <label className="field">
        <span className="field__label">{label}</span>
        <input
          className="input" name="map-name" value={value} autoComplete="off" spellCheck={false}
          onChange={(e) => setValue(e.target.value)} aria-invalid={!valid}
          autoFocus={window.matchMedia("(pointer: fine)").matches}
          onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }}
        />
        {!valid && <span className="field__error">{MAP_NAME_HINT}</span>}
      </label>
      <div className="mapname__actions">
        <IconButton label="Cancel" icon={X} variant="ghost" size="sm" onClick={onCancel} />
        <IconButton label="Save name" icon={Check} variant="primary" size="sm" type="submit" disabled={!valid || busy} />
      </div>
    </form>
  );
}

function MapsPanel({ stack, prefs, onEdit, editing }) {
  const toast = useToast();
  const { library, status, reachable, busy, client } = stack;
  const [naming, setNaming] = useState(null); // { name, kind: "rename" | "duplicate" }
  const [confirm, setConfirm] = useState(null);
  const running = status?.running;
  const inUse = running ? status.map : null;

  const fail = (e) => toast({ title: "That didn't work", body: e.message, tone: "error" });

  const launchWith = (opts, verb) => {
    const go = () => stack.relaunch({ target: prefs.target, rviz: prefs.rviz, ...opts })
      .then(() => toast({ title: verb }))
      .catch(fail);
    if (running) {
      setConfirm({
        title: opts.mode === "mapping" ? "Restart in mapping mode?" : `Restart navigation on ${opts.map}?`,
        body: `${describeStack(status)} stops first, and a running mission is cancelled. The robot keeps its position.`,
        label: opts.mode === "mapping" ? "Start mapping" : "Navigate",
        go,
      });
    } else go();
  };

  const remove = async (m) => {
    try {
      const r = await stack.remove(m.name);
      toast({
        title: `Deleted ${m.name}`,
        body: "Moved to the map trash on the robot computer.",
        action: { label: "Undo", onClick: () => stack.restore(r.trash_id).catch(fail) },
      });
    } catch (e) { fail(e); }
  };

  const named = async (m, value) => {
    try {
      if (naming.kind === "rename") await stack.rename(m.name, value);
      else await stack.duplicate(m.name, value);
      setNaming(null);
      toast({ title: naming.kind === "rename" ? `Renamed to ${value}` : `Duplicated as ${value}` });
    } catch (e) { fail(e); }
  };

  if (reachable === false) {
    return (
      <div className="panel">
        <p className="empty">
          Maps are managed by <code translate="no">launcher_server.py</code> on the robot computer. Start it there
          (<code translate="no">python3 src/amr_mission_control/launcher_server.py</code>) and this list fills in.
        </p>
      </div>
    );
  }

  const maps = library.maps;
  return (
    <div className="panel panel--maps">
      <div className="section-head">
        <h2>Saved maps</h2>
        <span className="count num">{maps.length}</span>
      </div>
      <Button
        icon={Radar}
        variant={maps.length ? "secondary" : "primary"}
        className="maps__new"
        disabled={busy === "launch" || status?.mode === "mapping"}
        onClick={() => launchWith({ mode: "mapping" }, "Mapping started. Drive the robot through the whole area.")}
      >
        {status?.mode === "mapping" ? "Mapping in progress" : "New map"}
      </Button>

      {maps.length === 0 ? (
        <p className="empty">No maps yet. Press <strong>New map</strong>, drive the robot around the site, then save it.</p>
      ) : (
        <ul className="maps">
          {maps.map((m) => {
            const isDefault = library.default === m.name;
            const active = inUse === m.name;
            if (naming?.name === m.name) {
              return (
                <li key={m.name} className="maprow maprow--form">
                  <NameForm
                    initial={naming.kind === "rename" ? m.name : `${m.name}_copy`.slice(0, 40)}
                    label={naming.kind === "rename" ? `Rename ${m.name}` : `Copy of ${m.name}`}
                    busy={!!busy}
                    onCancel={() => setNaming(null)}
                    onSubmit={(v) => named(m, v)}
                  />
                </li>
              );
            }
            return (
              <li key={m.name} className={cx("maprow", active && "is-active", editing === m.name && "is-editing")}>
                {client && <Thumb client={client} info={m} />}
                <div className="maprow__body">
                  <span className="maprow__name">{m.name}</span>
                  <span className="maprow__meta num">
                    {m.error ? "Unreadable map files" : m.size_m ? `${m.size_m[0]} × ${m.size_m[1]} m` : "No image"}
                    {m.modified ? `, ${m.edited ? "edited" : "saved"} ${ago(m.modified)}` : ""}
                  </span>
                  <span className="maprow__tags">
                    {active && <span className="tag tag--live">In use</span>}
                    {isDefault && <span className="tag">Default</span>}
                    {m.edited && <span className="tag">Edited</span>}
                    {!m.has_posegraph && <span className="tag tag--warn">No pose graph</span>}
                  </span>
                  {!active && m.has_posegraph && (
                    <Button size="sm" icon={Navigation} className="maprow__go" disabled={busy === "launch"} onClick={() => launchWith({ mode: "navigation", map: m.name }, `Navigation starting on ${m.name}.`)}>
                      Navigate with {m.name}
                    </Button>
                  )}
                </div>
                <div className="maprow__actions">
                  <Menu>
                    <MenuTrigger asChild>
                      <IconButton label={`More for ${m.name}`} icon={MoreHorizontal} variant="ghost" size="sm" />
                    </MenuTrigger>
                    <MenuContent align="end">
                      <MenuItem icon={Pencil} disabled={!m.has_image} onSelect={() => onEdit(m)} hint="Paint walls, erase noise">Edit map</MenuItem>
                      <MenuItem icon={TextCursorInput} disabled={active} onSelect={() => setNaming({ name: m.name, kind: "rename" })}>Rename</MenuItem>
                      <MenuItem icon={Copy} onSelect={() => setNaming({ name: m.name, kind: "duplicate" })}>Duplicate</MenuItem>
                      <MenuItem icon={Star} disabled={isDefault} onSelect={() => stack.setDefault(m.name).then(() => toast({ title: `${m.name} is the default map` })).catch(fail)}>
                        Set as default
                      </MenuItem>
                      {m.edited && (
                        <MenuItem icon={RotateCcw} onSelect={() => setConfirm({
                          title: `Revert ${m.name} to the scan?`,
                          body: "Every edit made in the map editor is discarded and the image saved by SLAM comes back.",
                          label: "Revert edits",
                          danger: true,
                          go: () => stack.revert(m.name).then(() => toast({ title: `${m.name} reverted` })).catch(fail),
                        })}>
                          Revert edits
                        </MenuItem>
                      )}
                      <MenuSeparator />
                      <MenuItem icon={Trash2} tone="danger" disabled={active} hint={active ? "Stop the stack first" : undefined} onSelect={() => remove(m)}>
                        Delete
                      </MenuItem>
                    </MenuContent>
                  </Menu>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <p className="hint">
        Maps live in <code translate="no">{library.dir || status?.maps_dir || "amr_navigation/maps"}</code>. Racks, zones and lanes are shared by every map.
      </p>
      {status?.running && !inUse && status.mode === "navigation" && <p className="hint">Navigation is running on a map that isn’t in this list.</p>}

      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(o) => { if (!o) setConfirm(null); }}
        title={confirm?.title}
        body={confirm?.body}
        confirmLabel={confirm?.label}
        tone={confirm?.danger ? "danger" : "primary"}
        onConfirm={() => confirm?.go()}
      />
    </div>
  );
}

export default memo(MapsPanel);
