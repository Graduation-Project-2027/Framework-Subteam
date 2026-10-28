/**
 * MapView — the live floor plan, drawn like RViz.
 *
 * A world-metre camera renders any occupancy grid (live SLAM map, saved or
 * edited map, costmaps) plus sensors, lanes, sites and the robot on its own
 * requestAnimationFrame loop, reading high-rate data straight from the live
 * store. Pointer events cover mouse, pen and touch: drag to pan, pinch or
 * wheel to zoom, double-click to send the robot, right-click a rack to flip
 * it. In map-editor mode, drags paint into the map instead.
 */
import { useRef, useEffect, useState, useCallback, useMemo, memo } from "react";
import { Plus, Minus, Maximize, LocateFixed, X, Check, Undo2, Spline } from "lucide-react";
import { loadBundledMap, gridBounds, knownBounds } from "../../lib/grid";
import { displayMap } from "../../lib/live";
import { laneGeometry, smoothPolyline, DEFAULT_RADIUS } from "../../lib/lanes";
import { paintStroke, PAINT_VALUES } from "../../lib/edit";
import { RACK_SIDE, fmt } from "../../lib/geometry";
import { stateMeta } from "../../lib/mission";
import { IconButton, Button } from "../ui";
import LayersMenu from "./LayersMenu";
import {
  C, drawRaster, drawGrid, drawLaneNetwork, drawLaneDraft, drawPlan, drawDock, drawSite, drawPoints,
  drawDynamic, drawFootprint, drawLocalWindow, drawTrail, drawRobot, drawPickArrow, drawBrush,
} from "./draw";

const MIN_S = 3;   // px per metre
const MAX_S = 320;
const clampS = (s) => Math.max(MIN_S, Math.min(MAX_S, s));
const lerp = (a, b, t) => a + (b - a) * t;
const lerpAngle = (a, b, t) => a + Math.atan2(Math.sin(b - a), Math.cos(b - a)) * t;
const NICE = [0.5, 1, 2, 5, 10, 20, 50];

function MapView({
  missionState, plan, sendCommand, live, layers, onLayer, stackMode,
  pick, laneDraft, onAddLanePoint, onUndoLanePoint, onFinishLane, onCancelLane,
  hoverId, onHoverId, laneHover, focus, offline, editor, children,
}) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [follow, setFollow] = useState(false);
  const bundledRef = useRef(null);

  const radius = missionState.lane_corner_radius ?? DEFAULT_RADIUS;
  const lanes = missionState.locations.lanes;
  const geo = useMemo(() => laneGeometry(lanes, radius), [lanes, radius]);
  const highlight = useMemo(() => {
    const lane = lanes.find((l) => l.id === laneHover);
    return lane ? smoothPolyline(lane.points, radius) : null;
  }, [lanes, laneHover, radius]);
  const draftSmooth = useMemo(() => (laneDraft?.points?.length > 1 ? smoothPolyline(laneDraft.points, radius) : []), [laneDraft, radius]);

  const props = useRef({});
  props.current = { missionState, plan, laneDraft, hoverId, pick, follow, offline, layers, geo, highlight, draftSmooth, editor, stackMode };

  const view = useRef({ cx: 0, cy: 0, s: 20, fitted: false, userMoved: false, mapRef: null, sites: undefined });
  const anim = useRef(null);
  const dispPose = useRef(null);
  const trail = useRef([]);
  const pickArrow = useRef(null);
  const cursor = useRef(null);
  const pointers = useRef(new Map());
  const gesture = useRef(null);
  const space = useRef(false);
  const scaleBarRef = useRef(null);
  const scaleLabelRef = useRef(null);
  const coordRef = useRef(null);
  const reduceMotion = useRef(false);

  useEffect(() => {
    const m = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduceMotion.current = m.matches;
    const on = () => { reduceMotion.current = m.matches; };
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);

  useEffect(() => {
    let alive = true;
    loadBundledMap().then((g) => { if (alive) bundledRef.current = g; }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => { if (stackMode === "mapping") setFollow(true); }, [stackMode]);

  /* ── camera ───────────────────────────────────────────────────────── */
  const cam = useCallback(() => {
    const v = view.current;
    const W = wrapRef.current?.clientWidth || 1, H = wrapRef.current?.clientHeight || 1;
    return {
      W, H,
      s: v.s,
      sx: (x) => W / 2 + (x - v.cx) * v.s,
      sy: (y) => H / 2 - (y - v.cy) * v.s,
      wx: (px) => v.cx + (px - W / 2) / v.s,
      wy: (py) => v.cy - (py - H / 2) / v.s,
    };
  }, []);

  const currentMap = useCallback(() => {
    const p = props.current;
    if (p.editor) return p.editor.grid;
    return displayMap(live, p.stackMode) || (p.stackMode === "mapping" ? null : bundledRef.current);
  }, [live]);

  const contentBounds = useCallback(() => {
    const p = props.current;
    if (p.editor) return knownBounds(p.editor.grid) || gridBounds(p.editor.grid);
    const loc = p.missionState.locations;
    const pts = [...loc.racks, ...loc.drop_zones];
    if (pts.length && p.stackMode !== "mapping") {
      if (Number.isFinite(loc.dock?.x)) pts.push(loc.dock);
      if (!p.offline) pts.push(p.missionState.robot_pose);
      const b = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
      pts.forEach((q) => { b.x0 = Math.min(b.x0, q.x); b.x1 = Math.max(b.x1, q.x); b.y0 = Math.min(b.y0, q.y); b.y1 = Math.max(b.y1, q.y); });
      return { x0: b.x0 - 2.5, x1: b.x1 + 2.5, y0: b.y0 - 2.5, y1: b.y1 + 2.5 };
    }
    const g = currentMap();
    const kb = g && knownBounds(g);
    if (kb) return kb;
    const r = p.missionState.robot_pose;
    return { x0: r.x - 6, x1: r.x + 6, y0: r.y - 6, y1: r.y + 6 };
  }, [currentMap]);

  const animateTo = useCallback((target) => {
    if (reduceMotion.current) { Object.assign(view.current, target); return; }
    anim.current = { from: { ...view.current }, to: target, t0: performance.now(), dur: 320 };
  }, []);

  const fit = useCallback((instant = false) => {
    const { W, H } = cam();
    const b = contentBounds();
    const pad = 48;
    const s = clampS(Math.min((W - pad * 2) / Math.max(1, b.x1 - b.x0), (H - pad * 2) / Math.max(1, b.y1 - b.y0)));
    const target = { s, cx: (b.x0 + b.x1) / 2, cy: (b.y0 + b.y1) / 2 };
    if (instant) Object.assign(view.current, target);
    else animateTo(target);
  }, [contentBounds, animateTo, cam]);

  const zoomAt = useCallback((factor, px, py) => {
    const v = view.current;
    const c = cam();
    if (px == null) { px = c.W / 2; py = c.H / 2; }
    const wx = c.wx(px), wy = c.wy(py);
    v.s = clampS(v.s * factor);
    v.cx = wx - (px - c.W / 2) / v.s;
    v.cy = wy + (py - c.H / 2) / v.s;
    v.userMoved = true;
    anim.current = null;
  }, [cam]);

  const centerOn = useCallback((x, y, minScale = 60) => {
    animateTo({ s: Math.max(view.current.s, minScale), cx: x, cy: y });
  }, [animateTo]);

  useEffect(() => { if (focus) centerOn(focus.x, focus.y); }, [focus, centerOn]);
  useEffect(() => { if (editor?.name) { view.current.userMoved = false; fit(); } }, [editor?.name, fit]);

  /* ── render loop ──────────────────────────────────────────────────── */
  useEffect(() => {
    let raf;
    let last = performance.now();
    const frame = (ts) => {
      raf = requestAnimationFrame(frame);
      const canvas = canvasRef.current;
      const wrap = wrapRef.current;
      if (!canvas || !wrap) return;
      const dt = Math.min(0.05, (ts - last) / 1000);
      last = ts;
      const W = wrap.clientWidth, H = wrap.clientHeight;
      if (!W || !H) return;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
        canvas.width = Math.round(W * dpr);
        canvas.height = Math.round(H * dpr);
        canvas.style.width = `${W}px`;
        canvas.style.height = `${H}px`;
        if (!view.current.userMoved) fit(true);
      }
      const p = props.current;
      const v = view.current;
      const L = p.layers;
      const st = p.missionState;
      const loc = st.locations;
      const map = currentMap();

      if (!v.fitted && (map || loc.racks.length)) { fit(true); v.fitted = true; }
      const siteCount = loc.racks.length + loc.drop_zones.length;
      const mapKey = map ? `${map.width}:${map.height}:${map.origin.x.toFixed(2)}:${map.origin.y.toFixed(2)}` : null;
      if (mapKey !== v.mapRef || siteCount !== v.sites) {
        const changed = v.mapRef != null && mapKey != null;
        v.mapRef = mapKey;
        v.sites = siteCount;
        if (changed && !v.userMoved && !p.editor && !p.follow) fit();
      }

      if (anim.current) {
        const a = anim.current;
        const k = Math.min(1, (ts - a.t0) / a.dur);
        const e = 1 - Math.pow(1 - k, 3);
        v.cx = lerp(a.from.cx, a.to.cx, e);
        v.cy = lerp(a.from.cy, a.to.cy, e);
        v.s = lerp(a.from.s, a.to.s, e);
        if (k >= 1) anim.current = null;
      }

      // smoothed robot pose
      let d = null;
      const target = p.offline || p.editor ? null : st.robot_pose;
      if (!target) { dispPose.current = null; trail.current = []; }
      if (target && Number.isFinite(target.x)) {
        if (!dispPose.current) dispPose.current = { ...target };
        d = dispPose.current;
        const a = 1 - Math.exp(-10 * dt);
        d.x = lerp(d.x, target.x, a);
        d.y = lerp(d.y, target.y, a);
        d.yaw = lerpAngle(d.yaw, target.yaw || 0, a);
        const tr = trail.current;
        const lp = tr[tr.length - 1];
        if (!lp || Math.hypot(lp.x - d.x, lp.y - d.y) > 0.05) {
          tr.push({ x: d.x, y: d.y });
          if (tr.length > 120) tr.shift();
        }
        if (p.follow && !anim.current) {
          const k = 1 - Math.exp(-6 * dt);
          v.cx = lerp(v.cx, d.x, k);
          v.cy = lerp(v.cy, d.y, k);
        }
      }

      const V = cam();
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.fillStyle = C.unknown;
      ctx.fillRect(0, 0, W, H);

      if (map && (L.map || p.editor)) drawRaster(ctx, map, "map", V);
      if (L.grid) drawGrid(ctx, V, map ? gridBounds(map) : { x0: V.wx(0), x1: V.wx(W), y0: V.wy(H), y1: V.wy(0) });

      const nav = !p.editor && !p.offline;
      if (nav && L.globalCostmap && live.globalCostmap) drawRaster(ctx, live.globalCostmap, "cost", V);
      if (nav && L.localCostmap && live.localCostmap) {
        drawRaster(ctx, live.localCostmap, "cost", V);
        drawLocalWindow(ctx, V, live.localCostmap);
      }
      if (L.lanes && !p.editor) drawLaneNetwork(ctx, V, p.geo, { highlight: p.highlight });
      if (p.laneDraft) drawLaneDraft(ctx, V, p.laneDraft.points, p.draftSmooth, cursor.current);
      if (nav && L.rackObstacles && live.rackCloud) drawPoints(ctx, V, live.rackCloud, C.goldInk, 4);
      if (nav && L.path && p.plan?.length > 1) drawPlan(ctx, V, p.plan);
      if (L.sites || p.editor) {
        if (Number.isFinite(loc.dock?.x) && !p.offline) drawDock(ctx, V, loc.dock, p.hoverId === "dock");
        loc.racks.forEach((r) => drawSite(ctx, V, r, "rack", p.hoverId === r.id, !!p.editor));
        loc.drop_zones.forEach((z) => drawSite(ctx, V, z, "zone", p.hoverId === z.id, !!p.editor));
      }
      if (nav) {
        if (L.lidarRear) drawPoints(ctx, V, live.scans.rear?.points, C.goldInk, 2.5);
        if (L.lidarFront) drawPoints(ctx, V, live.scans.front?.points, C.red, 2.5);
        if (L.lidar) drawPoints(ctx, V, live.scans.merged?.points, C.red, 3);
        if (L.dynamic && live.dynamic.length) drawDynamic(ctx, V, live.dynamic);
        if (L.footprint && live.footprint) drawFootprint(ctx, V, live.footprint);
      }
      if (d) {
        if (L.trail) drawTrail(ctx, V, trail.current);
        drawRobot(ctx, V, d, { carrying: st.carrying, live: stateMeta(st.state).tone === "live", t: ts, reduceMotion: reduceMotion.current });
      }
      if (pickArrow.current) drawPickArrow(ctx, pickArrow.current);
      if (p.editor && cursor.current && p.editor.tool === "brush") drawBrush(ctx, V, cursor.current, p.editor.size / 2, p.editor.value);
      if (p.editor && gesture.current?.kind === "line") {
        const g = gesture.current;
        ctx.save();
        ctx.strokeStyle = p.editor.value === "wall" ? C.ink : C.red;
        ctx.lineWidth = Math.max(2, p.editor.size * V.s);
        ctx.lineCap = "round";
        ctx.globalAlpha = 0.6;
        ctx.beginPath(); ctx.moveTo(V.sx(g.start.x), V.sy(g.start.y)); ctx.lineTo(V.sx(g.end.x), V.sy(g.end.y)); ctx.stroke();
        ctx.restore();
      }

      const meters = NICE.find((m) => m * v.s >= 64) || NICE[NICE.length - 1];
      if (scaleBarRef.current) scaleBarRef.current.style.width = `${Math.round(meters * v.s)}px`;
      if (scaleLabelRef.current) scaleLabelRef.current.textContent = `${meters} m`;
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [fit, currentMap, live, cam]);

  /* ── hit testing ──────────────────────────────────────────────────── */
  const toWorld = useCallback((clientX, clientY) => {
    const r = canvasRef.current.getBoundingClientRect();
    const c = cam();
    const px = clientX - r.left, py = clientY - r.top;
    return { px, py, x: Math.round(c.wx(px) * 100) / 100, y: Math.round(c.wy(py) * 100) / 100 };
  }, [cam]);

  const hitSite = useCallback((x, y) => {
    const loc = props.current.missionState.locations;
    const half = RACK_SIDE / 2;
    for (const l of [...loc.racks, ...loc.drop_zones]) {
      const yaw = l.yaw || 0;
      const dx = x - l.x, dy = y - l.y;
      const lx = dx * Math.cos(-yaw) - dy * Math.sin(-yaw);
      const ly = dx * Math.sin(-yaw) + dy * Math.cos(-yaw);
      if (Math.abs(lx) <= half && Math.abs(ly) <= half) return l;
    }
    const dk = loc.dock;
    if (dk && Math.hypot(x - dk.x, y - dk.y) < 0.5) return { ...dk, id: "dock" };
    return null;
  }, []);

  /* ── pointer input ────────────────────────────────────────────────── */
  const paint = (a, b) => {
    const ed = props.current.editor;
    if (paintStroke(ed.grid, a, b, ed.size / 2, PAINT_VALUES[ed.value])) ed.changed();
  };

  const onPointerDown = (e) => {
    const ed = props.current.editor;
    const panButton = e.button === 1 || e.button === 2 || space.current;
    if (e.pointerType === "mouse" && e.button !== 0 && !(ed && panButton)) return;
    try { canvasRef.current.setPointerCapture(e.pointerId); } catch { /* synthetic or lost pointer */ }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const p = toWorld(e.clientX, e.clientY);
    anim.current = null;

    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gesture.current = { kind: "pinch", dist: Math.hypot(a.x - b.x, a.y - b.y), mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 } };
      pickArrow.current = null;
      return;
    }
    if (ed && !panButton) {
      ed.begin();
      if (ed.tool === "line") gesture.current = { kind: "line", start: p, end: p };
      else { gesture.current = { kind: "paint", last: p }; paint(p, p); }
      return;
    }
    if (pick && !ed) {
      gesture.current = { kind: "pick", start: p };
      pickArrow.current = { sx: p.px, sy: p.py, ex: p.px, ey: p.py, yaw: 0 };
      return;
    }
    gesture.current = { kind: laneDraft && !panButton ? "lane" : "pan", sx: e.clientX, sy: e.clientY, cx: view.current.cx, cy: view.current.cy, moved: false };
  };

  const onPointerMove = (e) => {
    const p = toWorld(e.clientX, e.clientY);
    cursor.current = { x: p.x, y: p.y };
    if (coordRef.current) coordRef.current.textContent = `${fmt(p.x)}, ${fmt(p.y)} m`;
    const g = gesture.current;
    if (!g) {
      if (props.current.editor) return;
      const hit = hitSite(p.x, p.y);
      const id = hit ? hit.id : null;
      if (id !== props.current.hoverId) onHoverId?.(id);
      return;
    }
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (g.kind === "pinch" && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      const r = canvasRef.current.getBoundingClientRect();
      view.current.cx -= (mid.x - g.mid.x) / view.current.s;
      view.current.cy += (mid.y - g.mid.y) / view.current.s;
      zoomAt(dist / g.dist, mid.x - r.left, mid.y - r.top);
      g.dist = dist; g.mid = mid;
      return;
    }
    if (g.kind === "paint") { paint(g.last, p); g.last = p; return; }
    if (g.kind === "line") { g.end = p; return; }
    if (g.kind === "pick") {
      const dx = p.px - g.start.px, dy = p.py - g.start.py;
      pickArrow.current = { sx: g.start.px, sy: g.start.py, ex: p.px, ey: p.py, yaw: Math.atan2(-dy, dx) };
      return;
    }
    const dx = e.clientX - g.sx, dy = e.clientY - g.sy;
    if (Math.hypot(dx, dy) > 5) g.moved = true;
    if (g.moved) {
      view.current.cx = g.cx - dx / view.current.s;
      view.current.cy = g.cy + dy / view.current.s;
      view.current.userMoved = true;
      if (follow) setFollow(false);
    }
  };

  const onPointerUp = (e) => {
    const g = gesture.current;
    pointers.current.delete(e.pointerId);
    if (!g) return;
    if (g.kind === "pinch") {
      if (pointers.current.size === 0) gesture.current = null;
      return;
    }
    gesture.current = null;
    if (g.kind === "line") { paint(g.start, g.end); return; }
    if (g.kind === "pick") {
      const p = toWorld(e.clientX, e.clientY);
      const dx = p.px - g.start.px, dy = p.py - g.start.py;
      const yaw = Math.hypot(dx, dy) > 10 ? Math.atan2(-dy, dx) : 0;
      pickArrow.current = null;
      pick?.onPick({ x: g.start.x, y: g.start.y, yaw });
      return;
    }
    if (g.kind === "lane" && !g.moved) {
      const p = toWorld(e.clientX, e.clientY);
      onAddLanePoint?.(p.x, p.y);
    }
  };

  const onPointerCancel = (e) => {
    pointers.current.delete(e.pointerId);
    gesture.current = null;
    pickArrow.current = null;
  };

  const onPointerLeave = () => {
    if (coordRef.current) coordRef.current.textContent = "";
    if (!gesture.current) cursor.current = null;
    if (!gesture.current && props.current.hoverId) onHoverId?.(null);
  };

  const canSend = !offline && !editor && stackMode !== "mapping";
  const onDoubleClick = (e) => {
    if (laneDraft) { onFinishLane?.(); return; }
    if (pick || !canSend) return;
    const p = toWorld(e.clientX, e.clientY);
    sendCommand({ action: "navigate_to", x: p.x, y: p.y, yaw: 0, name: `(${p.x.toFixed(1)}, ${p.y.toFixed(1)})` });
  };

  const onContextMenu = (e) => {
    e.preventDefault();
    if (editor || offline) return;
    const p = toWorld(e.clientX, e.clientY);
    const hit = hitSite(p.x, p.y);
    if (!hit || hit.id === "dock") return;
    sendCommand({ action: "set_location_state", id: hit.id, state: (hit.state || "occupied") === "occupied" ? "free" : "occupied" });
  };

  // Non-passive wheel listener so the page doesn't scroll while zooming.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // Space-drag pans while editing.
  useEffect(() => {
    if (!editor) return undefined;
    const down = (e) => { if (e.code === "Space" && e.target === canvasRef.current) { space.current = true; e.preventDefault(); } };
    const up = (e) => { if (e.code === "Space") space.current = false; };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [editor]);

  const onKeyDown = (e) => {
    const v = view.current;
    const step = 60 / v.s;
    const keys = {
      ArrowLeft: () => { v.cx -= step; }, ArrowRight: () => { v.cx += step; },
      ArrowUp: () => { v.cy += step; }, ArrowDown: () => { v.cy -= step; },
      "+": () => zoomAt(1.25), "=": () => zoomAt(1.25), "-": () => zoomAt(0.8), "0": () => fit(),
      Escape: () => { if (laneDraft) onCancelLane?.(); else if (pick) pick.onCancel(); },
      Enter: () => { if (laneDraft) onFinishLane?.(); },
    };
    if (keys[e.key]) { e.preventDefault(); keys[e.key](); v.userMoved = true; }
  };

  const hovered = hoverId ? findSite(missionState, hoverId) : null;
  const mode = editor ? "edit" : pick ? "pick" : laneDraft ? "lane" : null;
  const legend = [
    layers.sites && !editor && ["occ", "Occupied"],
    layers.sites && !editor && ["free", "Free"],
    layers.lanes && !editor && ["lane", "Lane"],
    layers.path && canSend && ["path", "Planned path"],
    (layers.lidar || layers.lidarFront) && !editor && !offline && ["lidar", "Lidar"],
    layers.dynamic && !editor && !offline && ["dynamic", "Dynamic obstacle"],
    (layers.globalCostmap || layers.localCostmap) && canSend && ["cost", "Cost"],
    !editor && !offline && ["robot", "Robot"],
    editor && ["wall", "Wall"],
    editor && ["floor", "Free floor"],
    editor && ["unknown", "Unknown"],
  ].filter(Boolean);

  return (
    <section className="map" aria-label="Floor plan" data-mode={mode || undefined}>
      <div className="map__stage" ref={wrapRef}>
        <canvas
          ref={canvasRef}
          className="map__canvas"
          tabIndex={0}
          aria-label={editor
            ? "Map editor. Drag to paint, right-drag or Space-drag to pan, scroll or pinch to zoom."
            : "Floor plan map. Drag to pan, scroll or pinch to zoom, double-click to send the robot, right-click a rack to toggle free or occupied. Arrow keys pan, plus and minus zoom, 0 fits."}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerCancel}
          onPointerLeave={onPointerLeave}
          onDoubleClick={onDoubleClick}
          onContextMenu={onContextMenu}
          onKeyDown={onKeyDown}
        />
      </div>

      <div className="map__tools" role="toolbar" aria-label="Map view">
        <IconButton label="Zoom in" side="right" icon={Plus} variant="float" size="sm" onClick={() => zoomAt(1.3)} />
        <IconButton label="Zoom out" side="right" icon={Minus} variant="float" size="sm" onClick={() => zoomAt(1 / 1.3)} />
        <IconButton label="Fit map" side="right" icon={Maximize} variant="float" size="sm" onClick={() => { view.current.userMoved = false; setFollow(false); fit(); }} />
        {!editor && (
          <IconButton
            label={follow ? "Stop following robot" : "Follow robot"}
            side="right" icon={LocateFixed} variant="float" size="sm"
            aria-pressed={follow}
            className={follow ? "is-on" : undefined}
            onClick={() => setFollow((f) => !f)}
          />
        )}
        {!editor && <span className="map__tools-gap" aria-hidden="true" />}
        {!editor && (
          <IconButton
            label={layers.lanes ? "Hide lanes" : "Show lanes"}
            side="right" icon={Spline} variant="float" size="sm"
            aria-pressed={layers.lanes}
            className={layers.lanes ? "is-on" : undefined}
            onClick={() => onLayer("lanes", !layers.lanes)}
          />
        )}
        {!editor && <LayersMenu layers={layers} onLayer={onLayer} stackMode={stackMode} />}
      </div>

      {(mode === "pick" || mode === "lane") && (
        <div className="map__banner" role="status">
          {mode === "pick" ? (
            <>
              <span><strong>{pick.label}</strong> Press where it goes, drag to set heading.</span>
              <Button size="sm" variant="ghost-inverse" icon={X} onClick={pick.onCancel}>Cancel</Button>
            </>
          ) : (
            <>
              <span><strong>New lane</strong> {laneDraft.points.length === 0 ? "Click to place the first point." : `${laneDraft.points.length} point${laneDraft.points.length === 1 ? "" : "s"}. Corners are rounded; ends snap onto nearby lanes.`}</span>
              <Button size="sm" variant="ghost-inverse" icon={Undo2} onClick={onUndoLanePoint} disabled={!laneDraft.points.length}>Undo</Button>
              <Button size="sm" variant="ghost-inverse" icon={X} onClick={onCancelLane}>Cancel</Button>
              <Button size="sm" variant="inverse" icon={Check} onClick={onFinishLane} disabled={laneDraft.points.length < 2}>Finish</Button>
            </>
          )}
        </div>
      )}

      <div className="map__legend" aria-label="Legend">
        {legend.map(([k, label]) => <span key={k}><i className={`sw sw--${k}`} />{label}</span>)}
      </div>

      <div className="map__readout">
        {hovered && !editor && (
          <span className="map__hover">
            <b>{hovered.name || hovered.id}</b>
            {hovered.id === "dock" ? " dock" : ` ${hovered.state || "occupied"}`}
            {hovered.id !== "dock" && <em>Right-click to flip</em>}
          </span>
        )}
        <span className="map__coords" ref={coordRef} />
        <span className="map__scale"><i ref={scaleBarRef} /><span ref={scaleLabelRef} /></span>
      </div>

      {children}
    </section>
  );
}

function findSite(ms, id) {
  const l = ms?.locations || {};
  if (id === "dock") return { ...l.dock, id: "dock" };
  return [...(l.racks || []), ...(l.drop_zones || [])].find((x) => x.id === id) || null;
}

export default memo(MapView);
