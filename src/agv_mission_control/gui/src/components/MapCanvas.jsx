/**
 * MapCanvas — Renders the PGM-derived map with robot pose,
 * rack markers, dock, and click-to-navigate.
 */
import { useRef, useEffect, useState, useCallback } from "react";

const MAP_META = {
  resolution: 0.05,
  origin: [-22.024, -9.478],
  width: 726,
  height: 808,
};

/**
 * Convert world coordinates (meters) to pixel coordinates on the map image.
 */
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

export default function MapCanvas({ missionState, sendCommand }) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const imgRef = useRef(null);
  const [loaded, setLoaded] = useState(false);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 0.8 });
  const dragRef = useRef({ dragging: false, startX: 0, startY: 0 });

  // Load map image
  useEffect(() => {
    const img = new Image();
    img.src = "/map.png";
    img.onload = () => {
      imgRef.current = img;
      setLoaded(true);
    };
  }, []);

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
      // Robot body
      ctx.fillStyle = "#3b82f6";
      ctx.globalAlpha = 0.9;
      ctx.beginPath();
      ctx.moveTo(12, 0);
      ctx.lineTo(-8, -7);
      ctx.lineTo(-5, 0);
      ctx.lineTo(-8, 7);
      ctx.closePath();
      ctx.fill();
      // Glow ring
      ctx.globalAlpha = 0.2;
      ctx.strokeStyle = "#3b82f6";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(0, 0, 14, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }

    ctx.restore();
  }, [loaded, missionState, transform]);

  // Resize handler
  useEffect(() => {
    const obs = new ResizeObserver(() => {
      if (canvasRef.current && containerRef.current) {
        canvasRef.current.width = containerRef.current.clientWidth;
        canvasRef.current.height = containerRef.current.clientHeight;
        setLoaded((v) => v); // trigger re-render
      }
    });
    if (containerRef.current) obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, []);

  // Pan handlers
  const onMouseDown = useCallback((e) => {
    if (e.button === 0) {
      dragRef.current = { dragging: true, startX: e.clientX - transform.x, startY: e.clientY - transform.y };
    }
  }, [transform]);

  const onMouseMove = useCallback((e) => {
    if (dragRef.current.dragging) {
      setTransform((t) => ({
        ...t,
        x: e.clientX - dragRef.current.startX,
        y: e.clientY - dragRef.current.startY,
      }));
    }
  }, []);

  const onMouseUp = useCallback(() => {
    dragRef.current.dragging = false;
  }, []);

  // Zoom
  const onWheel = useCallback((e) => {
    e.preventDefault();
    setTransform((t) => ({
      ...t,
      scale: Math.max(0.2, Math.min(4, t.scale - e.deltaY * 0.001)),
    }));
  }, []);

  // Click to navigate
  const onDblClick = useCallback(
    (e) => {
      const canvas = canvasRef.current;
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;

      // Reverse the canvas transform
      const px =
        (cx - (transform.x + canvas.width / 2)) / transform.scale +
        MAP_META.width / 2;
      const py =
        (cy - (transform.y + canvas.height / 2)) / transform.scale +
        MAP_META.height / 2;

      const [wx, wy] = pixelToWorld(px, py);
      sendCommand({
        action: "navigate_to",
        x: Math.round(wx * 100) / 100,
        y: Math.round(wy * 100) / 100,
        yaw: 0,
        name: `(${wx.toFixed(1)}, ${wy.toFixed(1)})`,
      });
    },
    [transform, sendCommand]
  );

  return (
    <div className="map-area" ref={containerRef}>
      <canvas
        ref={canvasRef}
        className="map-canvas"
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
        onWheel={onWheel}
        onDoubleClick={onDblClick}
      />
      <div className="map-overlay">
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.min(4, t.scale + 0.2) }))}>
          +
        </button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform((t) => ({ ...t, scale: Math.max(0.2, t.scale - 0.2) }))}>
          −
        </button>
        <button className="btn btn--sm btn--icon" onClick={() => setTransform({ x: 0, y: 0, scale: 0.8 })}>
          ⟲
        </button>
      </div>
    </div>
  );
}
