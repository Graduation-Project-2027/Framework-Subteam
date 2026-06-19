/**
 * MapCanvas — live warehouse map.
 *
 * Renders on a requestAnimationFrame loop (decoupled from React) so the
 * robot pose can be smoothly interpolated between the 5 Hz state updates
 * instead of stepping/lagging.  Draws the map image, a metric grid, lanes,
 * racks/zones coloured by their actual free/occupied state, the dock, and
 * the robot (true footprint + heading + motion trail).  High-DPI aware.
 */
import { useRef, useEffect, useState, useCallback } from "react";

const MAP_META = {
  resolution: 0.05,
  origin: [-22.024, -9.478],
  width: 726,
  height: 808,
};

const RACK_SIDE = 0.95;            // m, rack footprint
const ROBOT_LEN = 0.70;            // m
const ROBOT_WID = 0.64;            // m
const PX_PER_M = 1 / MAP_META.resolution;

function worldToPixel(wx, wy) {
  return [
    (wx - MAP_META.origin[0]) / MAP_META.resolution,
    MAP_META.height - (wy - MAP_META.origin[1]) / MAP_META.resolution,
  ];
}
function pixelToWorld(px, py) {
  return [
    px * MAP_META.resolution + MAP_META.origin[0],
    (MAP_META.height - py) * MAP_META.resolution + MAP_META.origin[1],
  ];
}
const lerp = (a, b, t) => a + (b - a) * t;
function lerpAngle(a, b, t) {
  let d = ((b - a + Math.PI) % (2 * Math.PI)) - Math.PI;
  if (d < -Math.PI) d += 2 * Math.PI;
  return a + d * t;
}

export default function MapCanvas({
  missionState,
  sendCommand,
  onPickPosition,
  pickMode,
  laneDraft,
  onAddLanePoint,
  onFinishLaneDraft,
  plan,
}) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const imgRef = useRef(null);
  const [imgLoaded, setImgLoaded] = useState(false);

  // Live values the rAF loop reads (refs avoid restarting the loop).
  const stateRef = useRef(missionState);
  const laneDraftRef = useRef(laneDraft);
  const planRef = useRef(plan);
  const transformRef = useRef({ x: 0, y: 0, scale: 0.85 });
  const dispPoseRef = useRef(null);        // smoothed robot pose
  const trailRef = useRef([]);             // [{x,y,t}] world-frame breadcrumb
  const pickArrowRef = useRef(null);
  const lastTsRef = useRef(0);

  const [mouseWorld, setMouseWorld] = useState(null);
  const dragRef = useRef({ dragging: false, sx: 0, sy: 0 });
  const pickDragRef = useRef(null);

  useEffect(() => { stateRef.current = missionState; }, [missionState]);
  useEffect(() => { laneDraftRef.current = laneDraft; }, [laneDraft]);
  useEffect(() => { planRef.current = plan; }, [plan]);

  // Load map image
  useEffect(() => {
    const img = new Image();
    img.src = "/map.png";
    img.onload = () => { imgRef.current = img; setImgLoaded(true); };
  }, []);

  // ── Coordinate helpers (CSS pixels) ───────────────────────────────
  const mouseToMapPx = useCallback((e) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const t = transformRef.current;
    const px = (cx - (t.x + rect.width / 2)) / t.scale + MAP_META.width / 2;
    const py = (cy - (t.y + rect.height / 2)) / t.scale + MAP_META.height / 2;
    return { px, py, cx, cy };
  }, []);

  const mouseToWorld = useCallback((e) => {
    const p = mouseToMapPx(e);
    if (!p) return null;
    const [wx, wy] = pixelToWorld(p.px, p.py);
    return { x: Math.round(wx * 100) / 100, y: Math.round(wy * 100) / 100 };
  }, [mouseToMapPx]);

  // ── Render loop ───────────────────────────────────────────────────
  useEffect(() => {
    if (!imgLoaded) return;
    let raf;

    const draw = (ts) => {
      const canvas = canvasRef.current;
      const container = containerRef.current;
      if (!canvas || !container) { raf = requestAnimationFrame(draw); return; }

      const dt = Math.min(0.05, (ts - lastTsRef.current) / 1000 || 0.016);
      lastTsRef.current = ts;

      const dpr = window.devicePixelRatio || 1;
      const cssW = container.clientWidth;
      const cssH = container.clientHeight;
      if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
        canvas.width = cssW * dpr;
        canvas.height = cssH * dpr;
      }
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

      const t = transformRef.current;
      const st = stateRef.current || {};
      const loc = st.locations || {};

      ctx.save();
      ctx.translate(t.x + cssW / 2, t.y + cssH / 2);
      ctx.scale(t.scale, t.scale);
      ctx.translate(-MAP_META.width / 2, -MAP_META.height / 2);

      // Map image
      ctx.imageSmoothingEnabled = false;
      ctx.globalAlpha = 0.92;
      ctx.drawImage(imgRef.current, 0, 0);
      ctx.globalAlpha = 1;

      drawGrid(ctx, t.scale);

      // Lanes
      (loc.lanes || []).forEach((lane) =>
        drawLane(ctx, lane.points, "#22d3ee", false, t.scale));
      if (laneDraftRef.current?.points?.length)
        drawLane(ctx, laneDraftRef.current.points, "#fbbf24", true, t.scale);

      // Nav2 global plan (live path)
      if (planRef.current?.length > 1) drawPlan(ctx, planRef.current, t.scale);

      // Dock
      if (loc.dock && loc.dock.x !== undefined) drawDock(ctx, loc.dock, t.scale);

      // Racks + zones (state-coloured)
      (loc.racks || []).forEach((r) => drawRack(ctx, r, "rack", t.scale));
      (loc.drop_zones || []).forEach((z) => drawRack(ctx, z, "zone", t.scale));

      // ── Robot (smoothed) ──
      const target = st.robot_pose;
      if (target && Number.isFinite(target.x)) {
        if (!dispPoseRef.current) dispPoseRef.current = { ...target };
        const d = dispPoseRef.current;
        const a = 1 - Math.exp(-12 * dt);   // smoothing factor
        d.x = lerp(d.x, target.x, a);
        d.y = lerp(d.y, target.y, a);
        d.yaw = lerpAngle(d.yaw, target.yaw || 0, a);

        // Trail
        const trail = trailRef.current;
        const last = trail[trail.length - 1];
        if (!last || Math.hypot(last.x - d.x, last.y - d.y) > 0.05) {
          trail.push({ x: d.x, y: d.y });
          if (trail.length > 60) trail.shift();
        }
        drawTrail(ctx, trail, t.scale);
        drawRobot(ctx, d, st.carrying, t.scale);
      }

      ctx.restore();

      // Pick arrow (screen space)
      if (pickArrowRef.current) drawPickArrow(ctx, pickArrowRef.current);

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [imgLoaded]);

  // ── Mouse handlers ────────────────────────────────────────────────
  const onMouseDown = useCallback((e) => {
    if (e.button !== 0) return;
    if (laneDraft) {
      const w = mouseToWorld(e);
      if (w && onAddLanePoint) onAddLanePoint(w.x, w.y);
      return;
    }
    if (pickMode) {
      const mp = mouseToMapPx(e);
      const w = mouseToWorld(e);
      if (mp && w) {
        pickDragRef.current = { cx: mp.cx, cy: mp.cy, worldX: w.x, worldY: w.y };
        pickArrowRef.current = { sx: mp.cx, sy: mp.cy, ex: mp.cx, ey: mp.cy, yaw: 0 };
      }
      return;
    }
    dragRef.current = { dragging: true, sx: e.clientX - transformRef.current.x, sy: e.clientY - transformRef.current.y };
  }, [pickMode, laneDraft, onAddLanePoint, mouseToMapPx, mouseToWorld]);

  const onMouseMove = useCallback((e) => {
    const w = mouseToWorld(e);
    if (w) setMouseWorld(w);

    if (pickMode && pickDragRef.current) {
      const rect = canvasRef.current.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const dx = mx - pickDragRef.current.cx, dy = my - pickDragRef.current.cy;
      pickArrowRef.current = { sx: pickDragRef.current.cx, sy: pickDragRef.current.cy, ex: mx, ey: my, yaw: Math.atan2(-dy, dx) };
      return;
    }
    if (dragRef.current.dragging) {
      transformRef.current.x = e.clientX - dragRef.current.sx;
      transformRef.current.y = e.clientY - dragRef.current.sy;
    }
  }, [mouseToWorld, pickMode]);

  const onMouseUp = useCallback((e) => {
    if (pickMode && pickDragRef.current) {
      const pd = pickDragRef.current;
      const rect = canvasRef.current.getBoundingClientRect();
      const dx = (e.clientX - rect.left) - pd.cx, dy = (e.clientY - rect.top) - pd.cy;
      const yaw = Math.hypot(dx, dy) > 8 ? Math.atan2(-dy, dx) : 0;
      pickDragRef.current = null;
      pickArrowRef.current = null;
      onPickPosition?.({ x: pd.worldX, y: pd.worldY, yaw });
      return;
    }
    dragRef.current.dragging = false;
  }, [pickMode, onPickPosition]);

  const onMouseLeave = useCallback(() => {
    dragRef.current.dragging = false;
    pickDragRef.current = null;
    pickArrowRef.current = null;
    setMouseWorld(null);
  }, []);

  // Zoom toward cursor
  const onWheel = useCallback((e) => {
    e.preventDefault();
    const mp = mouseToMapPx(e);
    const t = transformRef.current;
    const next = Math.max(0.2, Math.min(6, t.scale * (1 - e.deltaY * 0.0015)));
    if (mp) {
      t.x = mp.cx - (mp.px - MAP_META.width / 2) * next - canvasRef.current.getBoundingClientRect().width / 2;
      t.y = mp.cy - (mp.py - MAP_META.height / 2) * next - canvasRef.current.getBoundingClientRect().height / 2;
    }
    t.scale = next;
  }, [mouseToMapPx]);

  const onContextMenu = useCallback((e) => {
    e.preventDefault();
    const w = mouseToWorld(e);
    if (!w) return;
    const half = RACK_SIDE / 2;
    const all = [...(missionState.locations?.racks || []), ...(missionState.locations?.drop_zones || [])];
    for (const l of all) {
      const yaw = l.yaw || 0;
      const dx = w.x - l.x, dy = w.y - l.y;
      const lx = dx * Math.cos(-yaw) - dy * Math.sin(-yaw);
      const ly = dx * Math.sin(-yaw) + dy * Math.cos(-yaw);
      if (Math.abs(lx) <= half && Math.abs(ly) <= half) {
        const next = (l.state || "occupied") === "occupied" ? "free" : "occupied";
        sendCommand({ action: "set_location_state", id: l.id, state: next });
        return;
      }
    }
  }, [mouseToWorld, missionState.locations, sendCommand]);

  const onDblClick = useCallback((e) => {
    if (laneDraft) { onFinishLaneDraft?.(); return; }
    if (pickMode) return;
    const w = mouseToWorld(e);
    if (!w) return;
    sendCommand({ action: "navigate_to", x: w.x, y: w.y, yaw: 0, name: `(${w.x.toFixed(1)}, ${w.y.toFixed(1)})` });
  }, [mouseToWorld, sendCommand, pickMode, laneDraft, onFinishLaneDraft]);

  const zoomBy = (f) => {
    const t = transformRef.current;
    t.scale = Math.max(0.2, Math.min(6, t.scale * f));
  };

  return (
    <div className="map-area" ref={containerRef}>
      <canvas
        ref={canvasRef}
        className={`map-canvas ${pickMode ? "map-canvas--picking" : ""}`}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseLeave}
        onWheel={onWheel}
        onDoubleClick={onDblClick}
        onContextMenu={onContextMenu}
      />

      {/* Legend */}
      <div className="map-legend">
        <span><i className="lg lg--free" />Free</span>
        <span><i className="lg lg--occ" />Occupied</span>
        <span><i className="lg lg--lane" />Lane</span>
        <span><i className="lg lg--plan" />Path</span>
        <span><i className="lg lg--robot" />Robot</span>
      </div>

      {mouseWorld && (
        <div className="map-coords">
          x&nbsp;{mouseWorld.x.toFixed(2)} &nbsp; y&nbsp;{mouseWorld.y.toFixed(2)}
        </div>
      )}
      {pickMode && <div className="map-pick-banner">📍 Click &amp; drag to set position + heading</div>}
      {laneDraft && (
        <div className="map-pick-banner">
          🛣 Lane — click to add points ({laneDraft.points.length}), double-click to finish
        </div>
      )}
      <div className="map-overlay">
        <button className="btn btn--sm btn--icon" onClick={() => zoomBy(1.25)}>+</button>
        <button className="btn btn--sm btn--icon" onClick={() => zoomBy(0.8)}>−</button>
        <button className="btn btn--sm btn--icon" onClick={() => { transformRef.current = { x: 0, y: 0, scale: 0.85 }; }}>⟲</button>
      </div>
    </div>
  );
}

/* ── Draw helpers (map-pixel space unless noted) ───────────────────── */
function drawGrid(ctx, scale) {
  const step = PX_PER_M; // 1 m
  ctx.lineWidth = 1 / scale;
  for (let i = 0; i <= MAP_META.width; i += step) {
    const major = Math.round(i / step) % 5 === 0;
    ctx.strokeStyle = major ? "rgba(148,163,184,0.10)" : "rgba(148,163,184,0.045)";
    ctx.beginPath(); ctx.moveTo(i, 0); ctx.lineTo(i, MAP_META.height); ctx.stroke();
  }
  for (let j = 0; j <= MAP_META.height; j += step) {
    const major = Math.round(j / step) % 5 === 0;
    ctx.strokeStyle = major ? "rgba(148,163,184,0.10)" : "rgba(148,163,184,0.045)";
    ctx.beginPath(); ctx.moveTo(0, j); ctx.lineTo(MAP_META.width, j); ctx.stroke();
  }
}

function drawLane(ctx, points, color, dashed, scale) {
  if (!points || points.length === 0) return;
  const pts = points.map((p) => worldToPixel(p.x, p.y));
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  // Glow
  ctx.strokeStyle = color;
  ctx.globalAlpha = 0.18;
  ctx.lineWidth = 9 / scale;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  // Core
  ctx.globalAlpha = 1;
  ctx.lineWidth = 2.5 / scale;
  if (dashed) ctx.setLineDash([8 / scale, 5 / scale]);
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  ctx.setLineDash([]);
  // Vertices
  pts.forEach(([x, y]) => {
    ctx.beginPath(); ctx.arc(x, y, 4 / scale, 0, Math.PI * 2);
    ctx.fillStyle = "#0a0e17"; ctx.fill();
    ctx.lineWidth = 2 / scale; ctx.strokeStyle = color; ctx.stroke();
  });
  ctx.restore();
}

function drawPlan(ctx, plan, scale) {
  const pts = plan.map((p) => worldToPixel(p.x, p.y));
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  // glow
  ctx.strokeStyle = "#a855f7";
  ctx.globalAlpha = 0.20;
  ctx.lineWidth = 8 / scale;
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  // core (dashed, animated feel via map)
  ctx.globalAlpha = 0.95;
  ctx.strokeStyle = "#c084fc";
  ctx.lineWidth = 2 / scale;
  ctx.setLineDash([7 / scale, 5 / scale]);
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawDock(ctx, dock, scale) {
  const [x, y] = worldToPixel(dock.x, dock.y);
  ctx.save();
  ctx.translate(x, y);
  const r = 9 / scale;
  ctx.fillStyle = "rgba(245,158,11,0.18)";
  ctx.beginPath(); ctx.arc(0, 0, r * 1.8, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f59e0b";
  ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#1a1a1a";
  ctx.font = `${11 / scale}px sans-serif`;
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText("⌂", 0, 0.5 / scale);
  ctx.restore();
  drawLabel(ctx, x, y - 16 / scale, dock.name || "Dock", "#f59e0b", scale);
}

function drawRack(ctx, loc, kind, scale) {
  const [x, y] = worldToPixel(loc.x, loc.y);
  const yaw = -(loc.yaw || 0);
  const occupied = (loc.state || (kind === "rack" ? "occupied" : "free")) === "occupied";
  const half = (RACK_SIDE / 2) * PX_PER_M;
  const stroke = occupied ? "#ef4444" : "#22c55e";
  const fill = occupied ? "rgba(239,68,68,0.20)" : "rgba(34,197,94,0.16)";

  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(yaw);
  ctx.fillStyle = fill;
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2 / scale;
  roundRect(ctx, -half, -half, half * 2, half * 2, 3 / scale);
  ctx.fill();
  ctx.stroke();
  // corner legs
  ctx.fillStyle = stroke;
  [[-half, -half], [half, -half], [half, half], [-half, half]].forEach(([lx, ly]) => {
    ctx.beginPath(); ctx.arc(lx, ly, 2.4 / scale, 0, Math.PI * 2); ctx.fill();
  });
  // heading tick (approach direction)
  ctx.strokeStyle = stroke;
  ctx.lineWidth = 2 / scale;
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(half, 0); ctx.stroke();
  ctx.restore();

  drawLabel(ctx, x, y - half - 9 / scale, loc.name || loc.id, stroke, scale);
}

function drawTrail(ctx, trail, scale) {
  if (trail.length < 2) return;
  ctx.save();
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  for (let i = 1; i < trail.length; i++) {
    const [x0, y0] = worldToPixel(trail[i - 1].x, trail[i - 1].y);
    const [x1, y1] = worldToPixel(trail[i].x, trail[i].y);
    ctx.globalAlpha = (i / trail.length) * 0.5;
    ctx.strokeStyle = "#3b82f6";
    ctx.lineWidth = 2.5 / scale;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
  }
  ctx.restore();
}

function drawRobot(ctx, pose, carrying, scale) {
  const [x, y] = worldToPixel(pose.x, pose.y);
  const yaw = -(pose.yaw || 0);
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(yaw);

  if (carrying) {
    const rh = (RACK_SIDE / 2) * PX_PER_M;
    ctx.strokeStyle = "rgba(139,92,246,0.9)";
    ctx.fillStyle = "rgba(139,92,246,0.12)";
    ctx.lineWidth = 1.5 / scale;
    roundRect(ctx, -rh, -rh, rh * 2, rh * 2, 3 / scale);
    ctx.fill(); ctx.stroke();
  }

  const hl = (ROBOT_LEN / 2) * PX_PER_M;
  const hw = (ROBOT_WID / 2) * PX_PER_M;
  // glow
  ctx.shadowColor = "rgba(59,130,246,0.7)";
  ctx.shadowBlur = 14;
  ctx.fillStyle = "rgba(59,130,246,0.85)";
  ctx.strokeStyle = "#93c5fd";
  ctx.lineWidth = 1.5 / scale;
  roundRect(ctx, -hl, -hw, hl * 2, hw * 2, 4 / scale);
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.stroke();
  // heading wedge
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.moveTo(hl * 0.95, 0);
  ctx.lineTo(hl * 0.2, -hw * 0.55);
  ctx.lineTo(hl * 0.2, hw * 0.55);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function drawLabel(ctx, x, y, text, color, scale) {
  if (!text) return;
  ctx.save();
  ctx.font = `600 ${11 / scale}px Inter, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
  const w = ctx.measureText(text).width + 10 / scale;
  const h = 15 / scale;
  ctx.fillStyle = "rgba(10,14,23,0.78)";
  roundRect(ctx, x - w / 2, y - h, w, h, 4 / scale);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillText(text, x, y - 2.5 / scale);
  ctx.restore();
}

function drawPickArrow(ctx, a) {
  ctx.save();
  ctx.strokeStyle = "#fbbf24"; ctx.fillStyle = "#fbbf24"; ctx.lineWidth = 2.5;
  ctx.setLineDash([6, 4]);
  ctx.beginPath(); ctx.moveTo(a.sx, a.sy); ctx.lineTo(a.ex, a.ey); ctx.stroke();
  ctx.setLineDash([]);
  const ang = Math.atan2(a.ey - a.sy, a.ex - a.sx);
  ctx.beginPath();
  ctx.moveTo(a.ex, a.ey);
  ctx.lineTo(a.ex - 10 * Math.cos(ang - 0.4), a.ey - 10 * Math.sin(ang - 0.4));
  ctx.lineTo(a.ex - 10 * Math.cos(ang + 0.4), a.ey - 10 * Math.sin(ang + 0.4));
  ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.arc(a.sx, a.sy, 5, 0, Math.PI * 2); ctx.fill();
  ctx.font = "600 11px Inter, sans-serif"; ctx.fillStyle = "#fff"; ctx.textAlign = "left";
  ctx.fillText(`${((a.yaw * 180) / Math.PI).toFixed(0)}°`, a.ex + 8, a.ey - 4);
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}
