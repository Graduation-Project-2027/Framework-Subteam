import { useEffect, useState } from "react";
import { Save, Navigation, Brush, Slash, Undo2, Redo2, X, Check } from "lucide-react";
import { Button, IconButton, Segmented, Slider, Sun, ConfirmDialog, Kbd } from "./ui";
import { useLiveSelector } from "../lib/live";
import { MAP_NAME_RE, MAP_NAME_HINT } from "../lib/stackClient";
import { useToast } from "../lib/toast";

let areaCache = { map: null, version: -1, value: 0 };
function exploredArea(live) {
  const g = live.map;
  if (!g) return 0;
  if (areaCache.map === g && areaCache.version === g.version) return areaCache.value;
  let n = 0;
  for (let i = 0; i < g.data.length; i++) if (g.data[i] === 0) n++;
  areaCache = { map: g, version: g.version, value: Math.round(n * g.resolution * g.resolution) };
  return areaCache.value;
}

function suggestName(maps) {
  const names = new Set(maps.map((m) => m.name));
  for (let i = 1; ; i++) if (!names.has(`Site${i}`)) return `Site${i}`;
}

/** Shown over the map while SLAM runs: progress and Save. */
export function MappingBar({ stack, live, prefs }) {
  const toast = useToast();
  const area = useLiveSelector(live, exploredArea);
  const [name, setName] = useState(() => suggestName(stack.library.maps));
  const [saved, setSaved] = useState(null);
  const [overwrite, setOverwrite] = useState(false);
  const valid = MAP_NAME_RE.test(name);
  const exists = stack.library.maps.some((m) => m.name === name);
  const saving = stack.busy === "save";

  const save = (force = false) => stack.saveMap(name, force)
    .then(() => { setSaved(name); toast({ title: `Map saved as ${name}` }); })
    .catch((e) => toast({ title: "Map not saved", body: e.message, tone: "error" }));

  const navigate = () => stack.relaunch({ target: stack.status?.target || prefs.target, mode: "navigation", map: saved, rviz: prefs.rviz })
    .catch((e) => toast({ title: "Navigation didn't start", body: e.message, tone: "error" }));

  return (
    <div className="mapbar" role="region" aria-label="Mapping">
      <div className="mapbar__status">
        <Sun live size={10} />
        <div>
          <strong>Mapping</strong>
          <span className="num">{area} m² explored. Drive with <Kbd>W</Kbd><Kbd>A</Kbd><Kbd>S</Kbd><Kbd>D</Kbd>, slowly, closing loops.</span>
        </div>
      </div>
      {saved ? (
        <div className="mapbar__done">
          <span><Check size={14} strokeWidth={2.25} aria-hidden="true" /> Saved <strong>{saved}</strong></span>
          <Button size="sm" variant="ghost" onClick={() => setSaved(null)}>Save again</Button>
          <Button size="sm" variant="primary" icon={Navigation} disabled={stack.busy === "launch"} onClick={navigate}>Navigate with {saved}</Button>
        </div>
      ) : (
        <form className="mapbar__form" onSubmit={(e) => { e.preventDefault(); if (!valid || saving) return; if (exists) setOverwrite(true); else save(); }}>
          <label className="field">
            <span className="sr-only">Map name</span>
            <input className="input" name="map-name" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" spellCheck={false} aria-invalid={!valid} aria-describedby={valid ? undefined : "mapname-err"} />
          </label>
          <Button type="submit" size="sm" variant="primary" icon={Save} disabled={!valid || saving}>{saving ? "Saving…" : "Save map"}</Button>
          {!valid && <span className="field__error" id="mapname-err">{MAP_NAME_HINT}</span>}
        </form>
      )}
      <ConfirmDialog
        open={overwrite}
        onOpenChange={setOverwrite}
        title={`Replace ${name}?`}
        body="A saved map has this name. Its image and pose graph are replaced by this session, and any edits to it are lost."
        confirmLabel="Replace map"
        onConfirm={() => save(true)}
      />
    </div>
  );
}

/** Tools for painting into a saved map. */
export function MapEditorBar({ editor }) {
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const { tool, value, size, dirty, saving, undoCount, redoCount } = editor;

  useEffect(() => {
    const onKey = (e) => {
      const tag = document.activeElement?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); if (e.shiftKey) editor.redo(); else editor.undo(); return; }
      if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); editor.redo(); return; }
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (k === "b") editor.set({ tool: "brush" });
      if (k === "l") editor.set({ tool: "line" });
      if (k === "1") editor.set({ value: "wall" });
      if (k === "2") editor.set({ value: "free" });
      if (k === "3") editor.set({ value: "unknown" });
      if (k === "[") editor.set({ size: Math.max(0.05, +(size - 0.05).toFixed(2)) });
      if (k === "]") editor.set({ size: Math.min(1.5, +(size + 0.05).toFixed(2)) });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor, size]);

  const save = () => editor.save()
    .then(() => toast({ title: `${editor.name} saved`, body: "Planning uses it the next time navigation starts on this map." }))
    .catch((e) => toast({ title: "Edits not saved", body: e.message, tone: "error" }));

  return (
    <div className="editbar" role="toolbar" aria-label={`Editing ${editor.name}`}>
      <div className="editbar__title">
        <strong>{editor.name}</strong>
        <span>{dirty ? "Unsaved edits" : "No changes yet"}</span>
      </div>
      <Segmented
        size="sm"
        aria-label="Tool"
        value={tool}
        onValueChange={(v) => editor.set({ tool: v })}
        options={[
          { value: "brush", label: "Brush", icon: Brush, ariaLabel: "Brush (B)" },
          { value: "line", label: "Line", icon: Slash, ariaLabel: "Straight line (L)" },
        ]}
      />
      <Segmented
        size="sm"
        aria-label="Paint"
        value={value}
        onValueChange={(v) => editor.set({ value: v })}
        options={[
          { value: "wall", label: "Wall", ariaLabel: "Wall: blocks planning (1)" },
          { value: "free", label: "Free", ariaLabel: "Free floor: erase noise (2)" },
          { value: "unknown", label: "Unknown", ariaLabel: "Unknown (3)" },
        ]}
      />
      <div className="editbar__size">
        <span className="num">{size.toFixed(2)} m</span>
        <Slider aria-label="Brush size" min={0.05} max={1.5} step={0.05} value={size} onValueChange={(v) => editor.set({ size: v })} />
      </div>
      <div className="editbar__group">
        <IconButton label="Undo (Ctrl+Z)" icon={Undo2} variant="ghost" size="sm" disabled={!undoCount} onClick={editor.undo} />
        <IconButton label="Redo (Ctrl+Shift+Z)" icon={Redo2} variant="ghost" size="sm" disabled={!redoCount} onClick={editor.redo} />
      </div>
      <div className="editbar__group">
        <Button size="sm" variant="ghost" icon={X} onClick={() => (dirty ? setConfirm(true) : editor.close())}>Close</Button>
        <Button size="sm" variant="primary" icon={Save} disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save"}</Button>
      </div>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Discard your edits?"
        body={`The changes to ${editor.name} since you opened it are thrown away.`}
        confirmLabel="Discard edits"
        onConfirm={editor.close}
      />
    </div>
  );
}
