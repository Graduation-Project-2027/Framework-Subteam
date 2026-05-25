/**
 * MapCanvas — Renders the PGM-derived map with robot pose,
 * rack markers, dock, and click-to-navigate.
 * Supports pick mode: click to set position, drag to set heading.
 */
import { useRef, useEffect, useState, useCallback } from "react";

const MAP_META = {
  resolution: 0.05,
  origin: [-22.024, -9.478],
  width: 726,
  height: 808,
};

function worldToPixel(wx, wy) {
  const px = (wx - MAP_META.origin[0]) / MAP_META.resolution;
  const py = MAP_META.height - (wy - MAP_META.origin[1]) / MAP_META.resolution;
  return [px, py];
}

function pixelToWorld(px, py) {
  const wx = px * MAP_META.resolution + MAP_META.origin[0];
  const wy = (MAP_META.height - py) * MAP_META.resolution + MAP_META.origin[1];
  return [wx, wy];
}

export default function MapCanvas({
  missionState,
  sendCommand,
  onPickPosition,
  pickMode,
  laneDraft,
  onAddLanePoint,
  onFinishLaneDraft,
}) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const imgRef = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 0.8 });
  const [mouseWorld, setMouseWorld] = useState(null);
  const dragRef = useRef({ dragging: false, startX: 0, startY: 0 });
  // Pick-drag state: click sets position, drag direction sets yaw
  const pickDragRef = useRef(null); // { startPx, startPy, worldX, worldY }
  const [pickArrow, setPickArrow] = useState(null); // { sx, sy, ex, ey, yaw } in canvas coords

  // Load map image
  useEffect(() => {
    const img = new Image();
    img.src = "/map.png";
    img.onload = () => {
      imgRef.current = img;
      setLoaded(true);
    };
  }, []);

  // Convert mouse event to map-pixel coords
  const getMapPixelFromMouse = useCallback(
    (e) => {
      const canvas = canvasRef.current;
      if (!canvas) return null;
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const px =
        (cx - (transform.x + canvas.width / 2)) / transform.scale +
        MAP_META.width / 2;
      const py =
        (cy - (transform.y + canvas.height / 2)) / transform.scale +
        MAP_META.height / 2;
      return { px, py };
    },
    [transform]
  );

  const getWorldFromMouse = useCallback(
    (e) => {
      const p = getMapPixelFromMouse(e);
      if (!p) return null;
      const [wx, wy] = pixelToWorld(p.px, p.py);
      return { x: Math.round(wx * 100) / 100, y: Math.round(wy * 100) / 100 };
    },
    [getMapPixelFromMouse]
  );

  // Convert map pixel to canvas screen pixel
  const mapPxToScreen = useCallback(
    (mpx, mpy) => {
      const canvas = canvasRef.current;
      if (!canvas) return [0, 0];
      const sx = (mpx - MAP_META.width / 2) * transform.scale + transform.x + canvas.width / 2;
      const sy = (mpy - MAP_META.height / 2) * transform.scale + transform.y + canvas.height / 2;
      return [sx, sy];
    },
    [transform]
  );

  // Draw everything
  useEffect(() => {
    if (!loaded || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const container = containerRef.current;
    canvas.width = container.clientWidth;
    canvas.height = container.clientHeight;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(transform.x + canvas.width / 2, transform.y + canvas.height / 2);
    ctx.scale(transform.scale, transform.scale);
    ctx.translate(-MAP_META.width / 2, -MAP_META.height / 2);

    // Map image
    ctx.drawImage(imgRef.current, 0, 0);

    // ── Lanes (drawn first, under everything else) ─────────────────
    const drawLanePoly = (points, opts) => {
      if (!points || points.length === 0) return;
      ctx.save();
      // White halo behind the line so it stays visible against any map background
      if (opts.halo) {
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = (opts.lineWidth || 2) + 4;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.globalAlpha = 0.7;
        ctx.beginPath();
        points.forEach((p, i) => {
          const [px, py] = worldToPixel(p.x, p.y);
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.stroke();
        ctx.globalAlpha = 1;
      }
      ctx.strokeStyle = opts.stroke;
      ctx.fillStyle = opts.fill;
      ctx.lineWidth = opts.lineWidth || 2;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.setLineDash(opts.dash || []);
      ctx.beginPath();
      points.forEach((p, i) => {
        const [px, py] = worldToPixel(p.x, p.y);
        if (i === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.stroke();
      ctx.setLineDash([]);
      // Vertex dots with white outline
      points.forEach((p) => {
        const [px, py] = worldToPixel(p.x, p.y);
        ctx.beginPath();
        ctx.arc(px, py, (opts.dotRadius || 4) + 1, 0, Math.PI * 2);
        ctx.fillStyle = "#ffffff";
        ctx.fill();
        ctx.beginPath();
        ctx.arc(px, py, opts.dotRadius || 4, 0, Math.PI * 2);
        ctx.fillStyle = opts.fill;
        ctx.fill();
      });
      ctx.restore();
    };

    (missionState.locations?.lanes || []).forEach((lane) => {
      drawLanePoly(lane.points, {
        stroke: "#06b6d4",
        fill: "#06b6d4",
        lineWidth: 5,
        dotRadius: 5,
        halo: true,
      });
    });

    if (laneDraft && laneDraft.points.length > 0) {
      drawLanePoly(laneDraft.points, {
        stroke: "#f59e0b",
        fill: "#f59e0b",
        lineWidth: 5,
        dotRadius: 6,
        dash: [6, 4],
        halo: true,
      });
    }

    // Dock marker
    const dock = missionState.locations?.dock;
    if (dock && dock.x !== undefined) {
      const [dx, dy] = worldToPixel(dock.x, dock.y);
      ctx.save();
      ctx.translate(dx, dy);
      ctx.fillStyle = "#f59e0b";
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(0, 0, 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 0.3;
      ctx.beginPath();
      ctx.arc(0, 0, 16, 0, Math.PI * 2);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = "#fff";
      ctx.font = "bold 10px Inter";
      ctx.textAlign = "center";
      ctx.fillText("🏠", 0, -18);
      ctx.restore();
    }

    // Rack-square renderer — draws a 0.95m × 0.95m oriented square
    // coloured by state (green = free, red = occupied).  This is what
    // Nav2 sees as obstacles when "occupied" via the /rack_obstacles topic.
    const RACK_HALF_PX = (0.95 / 2) / MAP_META.resolution; // ≈ 9.5px
    const drawRackSquare = (loc, kind) => {
      const [px, py] = worldToPixel(loc.x, loc.y);
      const yaw = -(loc.yaw || 0); // canvas y is flipped
      const occupied = (loc.state || (kind === "rack" ? "occupied" : "free")) === "occupied";
      ctx.save();
      ctx.translate(px, py);
      ctx.rotate(yaw);
      // Filled square + border
      ctx.fillStyle = occupied ? "rgba(239, 68, 68, 0.35)" : "rgba(34, 197, 94, 0.30)";
      ctx.strokeStyle = occupied ? "#ef4444" : "#22c55e";
      ctx.lineWidth = 2;
      ctx.fillRect(-RACK_HALF_PX, -RACK_HALF_PX, RACK_HALF_PX * 2, RACK_HALF_PX * 2);
      ctx.strokeRect(-RACK_HALF_PX, -RACK_HALF_PX, RACK_HALF_PX * 2, RACK_HALF_PX * 2);
      // Heading tick (which way Nav2 will face when it arrives)
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.lineTo(RACK_HALF_PX, 0);
      ctx.stroke();
      ctx.restore();
      // Label (un-rotated)
      ctx.save();
      ctx.translate(px, py);
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "bold 10px Inter";
      ctx.textAlign = "center";
      ctx.fillText(loc.name || loc.id, 0, -RACK_HALF_PX - 6);
      ctx.restore();
    };

    (missionState.locations?.racks || []).forEach((r) => drawRackSquare(r, "rack"));
    (missionState.locations?.drop_zones || []).forEach((z) => drawRackSquare(z, "zone"));

    // Robot pose
    const pose = missionState.robot_pose;
    if (pose) {
      const [rpx, rpy] = worldToPixel(pose.x, pose.y);
      const yaw = -(pose.yaw || 0);
      ctx.save();
      ctx.translate(rpx, rpy);
      ctx.rotate(yaw);
      ctx.fillStyle = "#3b82f6";
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.moveTo(12, 0);
      ctx.lineTo(-8, -7);
      ctx.lineTo(-5, 0);
      ctx.lineTo(-8, 7);
      ctx.closePath();
      ctx.fill();
      ctx.globalAlpha = 0.2;
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(0, 0, 14, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    ctx.restore();

    // Draw pick arrow overlay (in screen space, after the main transform is restored)
    if (pickArrow) {
      ctx.save();
      ctx.strokeStyle = "#f59e0b";
      ctx.fillStyle = "#f59e0b";
      ctx.lineWidth = 2.5;
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(pickArrow.sx, pickArrow.sy);
      ctx.lineTo(pickArrow.ex, pickArrow.ey);
      ctx.stroke();
      ctx.setLineDash([]);
      // Arrow head
      const angle = Math.atan2(pickArrow.ey - pickArrow.sy, pickArrow.ex - pickArrow.sx);
      const hs = 10;
      ctx.beginPath();
      ctx.moveTo(pickArrow.ex, pickArrow.ey);
      ctx.lineTo(
        pickArrow.ex - hs * Math.cos(angle - 0.4),
        pickArrow.ey - hs * Math.sin(angle - 0.4)
      );
      ctx.lineTo(
        pickArrow.ex - hs * Math.cos(angle + 0.4),
        pickArrow.ey - hs * Math.sin(angle + 0.4)
      );
      ctx.closePath();
      ctx.fill();
      // Dot at start
      ctx.beginPath();
      ctx.arc(pickArrow.sx, pickArrow.sy, 5, 0, Math.PI * 2);
      ctx.fill();
      // Yaw text
      ctx.font = "bold 11px Inter";
      ctx.fillStyle = "#fff";
      ctx.textAlign = "left";
      ctx.fillText(
        `${(pickArrow.yaw * 180 / Math.PI).toFixed(0)}°`,
        pickArrow.ex + 8,
        pickArrow.ey - 4
      );
      ctx.restore();
    }
  }, [loaded, missionState, transform, pickArrow, laneDraft]);

  // Resize handler
  useEffect(() => {
    const obs = new ResizeObserver(() => {
      if (canvasRef.current && containerRef.current) {
        canvasRef.current.width = containerRef.current.clientWidth;
        canvasRef.current.height = containerRef.current.clientHeight;
        setLoaded((v) => v);
      }
    });
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  // Mouse handlers
  const onMouseDown = useCallback(
    (e) => {
      if (e.button !== 0) return;

      // Lane drawing: each click adds a vertex
      if (laneDraft) {
        const w = getWorldFromMouse(e);
        if (w && onAddLanePoint) onAddLanePoint(w.x, w.y);
        return;
      }

      if (pickMode) {
        // Start pick: record click position
        const mp = getMapPixelFromMouse(e);
        const w = getWorldFromMouse(e);
        if (mp && w) {
          const [sx, sy] = mapPxToScreen(mp.px, mp.py);
          pickDragRef.current = { screenX: sx, screenY: sy, worldX: w.x, worldY: w.y, mapPx: mp.px, mapPy: mp.py };
          setPickArrow({ sx, sy, ex: sx, ey: sy, yaw: 0 });
        }
        return;
      }

      // Normal pan
      dragRef.current = { dragging: true, startX: e.clientX - transform.x, startY: e.clientY - transform.y };
    },
    [pickMode, laneDraft, onAddLanePoint, transform, getMapPixelFromMouse, getWorldFromMouse, mapPxToScreen]
  );

  const onMouseMove = useCallback(
    (e) => {
      const w = getWorldFromMouse(e);
      if (w) setMouseWorld(w);

      // Pick drag — update arrow direction
      if (pickMode && pickDragRef.current) {
        const rect = canvasRef.current.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const dx = mx - pickDragRef.current.screenX;
        const dy = my - pickDragRef.current.screenY;
        const yaw = Math.atan2(-dy, dx); // Y flipped for screen coords → world yaw
        setPickArrow({
          sx: pickDragRef.current.screenX,
          sy: pickDragRef.current.screenY,
          ex: mx,
          ey: my,
          yaw,
        });
        return;
      }

      // Normal pan
      if (dragRef.current.dragging) {
        setTransform((t) => ({
          ...t,
          x: e.clientX - dragRef.current.startX,
          y: e.clientY - dragRef.current.startY,
        }));
      }
    },
    [getWorldFromMouse, pickMode]
  );

  const onMouseUp = useCallback(
    (e) => {
      if (pickMode && pickDragRef.current) {
        const pd = pickDragRef.current;
        const rect = canvasRef.current.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const dx = mx - pd.screenX;
        const dy = my - pd.screenY;
        const dist = Math.sqrt(dx * dx + dy * dy);
        // If barely dragged, yaw = 0; otherwise compute from drag direction
        const yaw = dist > 8 ? Math.atan2(-dy, dx) : 0;

        pickDragRef.current = null;
        setPickArrow(null);

        if (onPickPosition) {
          onPickPosition({ x: pd.worldX, y: pd.worldY, yaw });
        }
        return;
      }

      dragRef.current.dragging = false;
    },
    [pickMode, onPickPosition]
  );

  const onMouseLeave = useCallback(() => {
    dragRef.current.dragging = false;
    if (pickDragRef.current) {
      pickDragRef.current = null;
      setPickArrow(null);
    }
    setMouseWorld(null);
  }, []);

  // Zoom
  const onWheel = useCallback((e) => {
    e.preventDefault();
    setTransform((t) => ({
      ...t,
      scale: Math.max(0.2, Math.min(4, t.scale - e.deltaY * 0.001)),
    }));
  }, []);

  // Right-click toggles the state of the rack/zone under the cursor.
  // Free ↔ occupied — the mission_manager republishes /rack_obstacles
  // and Nav2 picks up the change on its next costmap update.
  const onContextMenu = useCallback(
    (e) => {
      e.preventDefault();
      const w = getWorldFromMouse(e);
      if (!w) return;
      const half = 0.95 / 2;
      const all = [
        ...(missionState.locations?.racks || []),
        ...(missionState.locations?.drop_zones || []),
      ];
      // Find the location whose square contains the click point (in its local frame).
      let hit = null;
      for (const loc of all) {
        const yaw = loc.yaw || 0;
        const dx = w.x - loc.x;
        const dy = w.y - loc.y;
        const lx = dx * Math.cos(-yaw) - dy * Math.sin(-yaw);
        const ly = dx * Math.sin(-yaw) + dy * Math.cos(-yaw);
        if (Math.abs(lx) <= half && Math.abs(ly) <= half) {
          hit = loc;
          break;
        }
      }
      if (!hit) return;
      const next = (hit.state || "occupied") === "occupied" ? "free" : "occupied";
      sendCommand({ action: "set_location_state", id: hit.id, state: next });
    },
    [getWorldFromMouse, missionState.locations, sendCommand]
  );

  // Double click to navigate, or finish lane draft if drawing
  const onDblClick = useCallback(
    (e) => {
      if (laneDraft) {
        // The first mousedown of the double-click already added a point;
        // commit the polyline now.
        if (onFinishLaneDraft) onFinishLaneDraft();
        return;
      }
      if (pickMode) return;
      const w = getWorldFromMouse(e);
      if (!w) return;
      sendCommand({
        action: "navigate_to",
        x: w.x,
        y: w.y,
        yaw: 0,
        name: `(${w.x.toFixed(1)}, ${w.y.toFixed(1)})`,
      });
    },
    [getWorldFromMouse, sendCommand, pickMode, laneDraft, onFinishLaneDraft]
  );

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
      {mouseWorld && (
        <div className="map-coords">
          x: {mouseWorld.x.toFixed(2)} &nbsp; y: {mouseWorld.y.toFixed(2)}
        </div>
      )}
      {pickMode && (
        <div className="map-pick-banner">📍 Click &amp; drag to set position + heading</div>
      )}
      {laneDraft && (
        <div className="map-pick-banner">
          🛣 Lane drawing — click to add points ({laneDraft.points.length}),
          double-click to finish
        </div>
      )}
      <div className="map-overlay">
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.min(4, t.scale + 0.2) }))}>+</button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.2, t.scale - 0.2) }))}>−</button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform({ x: 0, y: 0, scale: 0.8 })}>⟲</button>
      </div>
    </div>
  );
}
