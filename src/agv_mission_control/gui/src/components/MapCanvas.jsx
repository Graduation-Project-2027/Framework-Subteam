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

export default function MapCanvas({ missionState, sendCommand, onPickPosition, pickMode }) {
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

    // Rack markers
    const racks = missionState.locations?.racks || [];
    racks.forEach((rack) => {
      const [rx, ry] = worldToPixel(rack.x, rack.y);
      ctx.save();
      ctx.translate(rx, ry);
      ctx.fillStyle = "#8b5cf6";
      ctx.beginPath();
      ctx.arc(0, 0, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "bold 10px Inter";
      ctx.textAlign = "center";
      ctx.fillText(rack.name || rack.id, 0, -14);
      ctx.restore();
    });

    // Drop zones
    const zones = missionState.locations?.drop_zones || [];
    zones.forEach((zone) => {
      const [zx, zy] = worldToPixel(zone.x, zone.y);
      ctx.save();
      ctx.translate(zx, zy);
      ctx.fillStyle = "#10b981";
      ctx.beginPath();
      ctx.rect(-6, -6, 12, 12);
      ctx.fill();
      ctx.fillStyle = "#e2e8f0";
      ctx.font = "bold 10px Inter";
      ctx.textAlign = "center";
      ctx.fillText(zone.name || zone.id, 0, -14);
      ctx.restore();
    });

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
  }, [loaded, missionState, transform, pickArrow]);

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
    [pickMode, transform, getMapPixelFromMouse, getWorldFromMouse, mapPxToScreen]
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

  // Double click to navigate (only when NOT in pick mode)
  const onDblClick = useCallback(
    (e) => {
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
    [getWorldFromMouse, sendCommand, pickMode]
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
      />
      {mouseWorld && (
        <div className="map-coords">
          x: {mouseWorld.x.toFixed(2)} &nbsp; y: {mouseWorld.y.toFixed(2)}
        </div>
      )}
      {pickMode && (
        <div className="map-pick-banner">📍 Click &amp; drag to set position + heading</div>
      )}
      <div className="map-overlay">
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.min(4, t.scale + 0.2) }))}>+</button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.2, t.scale - 0.2) }))}>−</button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform({ x: 0, y: 0, scale: 0.8 })}>⟲</button>
      </div>
    </div>
  );
}
