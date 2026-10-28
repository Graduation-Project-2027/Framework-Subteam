import { useState, useEffect, useRef, useCallback } from "react";
import { gridFromPGM, gridToPGM, cloneGrid, touch } from "../lib/grid";

/** Launcher status, map library and actions (HTTP or Preview, same API). */
export function useStack(client) {
  const [status, setStatus] = useState(null);
  const [reachable, setReachable] = useState(null);
  const [library, setLibrary] = useState({ maps: [], default: null, in_use: null });
  const [busy, setBusy] = useState(null);
  const clientRef = useRef(client);
  clientRef.current = client;

  const poll = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return null;
    try {
      const s = await c.status();
      setStatus(s);
      setReachable(true);
      return s;
    } catch {
      setReachable(false);
      setStatus(null);
      return null;
    }
  }, []);

  const refreshMaps = useCallback(async () => {
    const c = clientRef.current;
    if (!c) return;
    try { setLibrary(await c.maps()); } catch { /* reported by status */ }
  }, []);

  useEffect(() => {
    if (!client) return undefined;
    let timer;
    let alive = true;
    setStatus(null);
    setReachable(null);
    const loop = async () => {
      const s = await poll();
      if (s) refreshMaps();
      if (alive) timer = setTimeout(loop, s ? (s.starting ? 500 : 2000) : 10000);
    };
    loop();
    return () => { alive = false; clearTimeout(timer); };
  }, [client, poll, refreshMaps]);

  const run = useCallback(async (label, fn) => {
    setBusy(label);
    try {
      const out = await fn(clientRef.current);
      await poll();
      await refreshMaps();
      return out;
    } finally {
      setBusy(null);
    }
  }, [poll, refreshMaps]);

  const actions = {
    launch: (opts) => run("launch", (c) => c.launch(opts)),
    stop: () => run("stop", (c) => c.stop()),
    relaunch: (opts) => run("launch", async (c) => {
      const s = await c.status();
      if (s.running) await c.stop();
      return c.launch(opts);
    }),
    rviz: (on) => run("rviz", (c) => c.rviz(on)),
    saveMap: (name, overwrite) => run("save", (c) => c.saveMap(name, overwrite)),
    rename: (name, to) => run("rename", (c) => c.rename(name, to)),
    duplicate: (name, to) => run("duplicate", (c) => c.duplicate(name, to)),
    setDefault: (name) => run("default", (c) => c.setDefault(name)),
    revert: (name) => run("revert", (c) => c.revert(name)),
    remove: (name) => run("delete", (c) => c.remove(name)),
    restore: (id) => run("restore", (c) => c.restore(id)),
    putPgm: (name, bytes) => run("save-edit", (c) => c.putPgm(name, bytes)),
  };

  return { client, status, reachable, library, busy, refreshMaps, ...actions };
}

/** Tail of the launcher output while `enabled`. */
export function useStackLogs(client, enabled) {
  const [lines, setLines] = useState([]);
  const after = useRef(0);
  useEffect(() => {
    after.current = 0;
    setLines([]);
  }, [client]);
  useEffect(() => {
    if (!client || !enabled) return undefined;
    let alive = true;
    let timer;
    const loop = async () => {
      try {
        const r = await client.logs(after.current);
        if (r.lines.length) {
          after.current = r.last;
          setLines((prev) => [...prev, ...r.lines].slice(-400));
        }
      } catch { /* launcher offline */ }
      if (alive) timer = setTimeout(loop, 1000);
    };
    loop();
    return () => { alive = false; clearTimeout(timer); };
  }, [client, enabled]);
  return lines;
}

const gridCache = new Map();

/** Saved map image as a grid (cached by name + modified time). */
export async function loadMapGrid(client, info) {
  const key = `${client.kind}:${info.name}:${info.modified}`;
  if (!gridCache.has(key)) {
    gridCache.set(key, client.pgm(info.name).then((buf) => gridFromPGM(buf, info)).catch((e) => {
      gridCache.delete(key);
      throw e;
    }));
  }
  return gridCache.get(key);
}

/**
 * Put the relevant saved map in live.mapFile: the map navigation runs on,
 * or the default map while the stack is stopped. Nothing while mapping.
 */
export function useMapFile(stack, live) {
  const { client, status, library } = stack;
  const name = status
    ? status.running ? (status.mode === "navigation" ? status.map : null) : status.default_map
    : null;
  const info = library.maps.find((m) => m.name === name && m.has_image);
  const key = info ? `${info.name}:${info.modified}` : null;
  useEffect(() => {
    let alive = true;
    if (!info || !client) {
      if (live.mapFile) { live.mapFile = null; live.notify(); }
      return undefined;
    }
    loadMapGrid(client, info).then((g) => {
      if (!alive) return;
      live.mapFile = g;
      live.notify();
    }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, key, live]);
}

/** Map editor state: a working grid, brush settings and undo/redo. */
export function useMapEditor(stack) {
  const [ed, setEd] = useState(null);
  const edRef = useRef(null);
  edRef.current = ed;
  const undo = useRef([]);
  const redo = useRef([]);

  const open = useCallback(async (info) => {
    const g = cloneGrid(await loadMapGrid(stack.client, info));
    undo.current = [];
    redo.current = [];
    setEd({ name: info.name, info, grid: g, tool: "brush", value: "wall", size: 0.2, dirty: false, saving: false, undoCount: 0, redoCount: 0 });
  }, [stack.client]);

  const set = useCallback((patch) => setEd((e) => (e ? { ...e, ...patch } : e)), []);

  const begin = useCallback(() => {
    const e = edRef.current;
    if (!e) return;
    undo.current.push(new Int8Array(e.grid.data));
    if (undo.current.length > 40) undo.current.shift();
    redo.current = [];
    setEd((x) => x && { ...x, undoCount: undo.current.length, redoCount: 0 });
  }, []);

  const changed = useCallback(() => {
    if (edRef.current && !edRef.current.dirty) setEd((x) => x && { ...x, dirty: true });
  }, []);

  const step = useCallback((from, to) => {
    const e = edRef.current;
    if (!e || !from.current.length) return;
    to.current.push(new Int8Array(e.grid.data));
    e.grid.data.set(from.current.pop());
    touch(e.grid);
    setEd((x) => x && { ...x, dirty: true, undoCount: undo.current.length, redoCount: redo.current.length });
  }, []);

  const doUndo = useCallback(() => step(undo, redo), [step]);
  const doRedo = useCallback(() => step(redo, undo), [step]);

  const save = useCallback(async () => {
    const e = edRef.current;
    if (!e) return;
    set({ saving: true });
    try {
      await stack.putPgm(e.name, gridToPGM(e.grid));
      setEd(null);
    } catch (err) {
      set({ saving: false });
      throw err;
    }
  }, [stack, set]);

  const close = useCallback(() => setEd(null), []);

  const editor = ed && {
    ...ed,
    begin,
    changed,
    set,
    undo: doUndo,
    redo: doRedo,
    save,
    close,
  };
  return { editor, open };
}
