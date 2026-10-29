/**
 * useROS — rosbridge connection with automatic reconnect.
 *
 * Mission topics are always subscribed. Visualisation topics (the ones the
 * RViz configs show) are subscribed only while a layer needs them, with CBOR
 * so grids and scans arrive as typed arrays, and written to the live store.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import * as ROSLIB from "roslib";
import { DEFAULT_STATE, normalizeState } from "../lib/mission";
import { gridFromMsg, toFloats, toBytes } from "../lib/grid";
import { scanToMap, detectDynamic } from "../lib/live";
import { apply } from "../lib/tf";

export const WS_URL = `ws://${window.location.hostname || "localhost"}:9090`;
export const rosUrl = (host, port = 9090) => `ws://${host}:${port}`;
const BACKOFF = [1, 2, 3, 5, 8];

const GRID = "nav_msgs/msg/OccupancyGrid";
const SCAN = "sensor_msgs/msg/LaserScan";
export const SENSOR_TOPICS = {
  tf: { name: "/tf", type: "tf2_msgs/msg/TFMessage", throttle: 0 },
  tfStatic: { name: "/tf_static", type: "tf2_msgs/msg/TFMessage", throttle: 0 },
  map: { name: "/map", type: GRID, throttle: 1000 },
  mapStatic: { name: "/map_static", type: GRID, throttle: 1000 },
  globalCostmap: { name: "/global_costmap/costmap", type: GRID, throttle: 1000 },
  localCostmap: { name: "/local_costmap/costmap", type: GRID, throttle: 400 },
  scanMerged: { name: "/scan_merged", type: SCAN, throttle: 100 },
  scanFront: { name: "/scan", type: SCAN, throttle: 100 },
  scanRear: { name: "/scan_rear", type: SCAN, throttle: 100 },
  rackCloud: { name: "/rack_obstacles", type: "sensor_msgs/msg/PointCloud2", throttle: 500 },
  footprint: { name: "/local_costmap/published_footprint", type: "geometry_msgs/msg/PolygonStamped", throttle: 200 },
};

const SCAN_KEYS = { scanMerged: "merged", scanFront: "front", scanRear: "rear" };

function readCloud(msg) {
  const bytes = toBytes(msg.data);
  const fx = msg.fields.find((f) => f.name === "x");
  const fy = msg.fields.find((f) => f.name === "y");
  if (!fx || !fy || !bytes.length) return new Float32Array(0);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const n = Math.floor(bytes.length / msg.point_step);
  const out = new Float32Array(n * 2);
  const le = !msg.is_bigendian;
  for (let i = 0; i < n; i++) {
    out[i * 2] = view.getFloat32(i * msg.point_step + fx.offset, le);
    out[i * 2 + 1] = view.getFloat32(i * msg.point_step + fy.offset, le);
  }
  return out;
}

function handlerFor(key, live) {
  const mark = () => { live.seen[key] = performance.now(); live.notify(); };
  if (key === "tf" || key === "tfStatic") return (msg) => { live.tf.ingest(msg); live.seen[key] = performance.now(); };
  if (key in SCAN_KEYS) {
    return (msg) => {
      const pts = scanToMap(msg, live.tf, toFloats(msg.ranges));
      if (!pts) return;
      live.scans[SCAN_KEYS[key]] = { points: pts, t: performance.now() };
      if (key === "scanMerged") live.dynamic = detectDynamic(pts, live.mapStatic || live.mapFile || live.map);
      mark();
    };
  }
  if (key === "rackCloud") {
    return (msg) => {
      const raw = readCloud(msg);
      const T = live.tf.lookup("map", msg.header.frame_id) || { x: 0, y: 0, yaw: 0 };
      for (let i = 0; i < raw.length; i += 2) [raw[i], raw[i + 1]] = apply(T, raw[i], raw[i + 1]);
      live.rackCloud = raw;
      mark();
    };
  }
  if (key === "footprint") {
    return (msg) => {
      const T = live.tf.lookup("map", msg.header.frame_id);
      if (!T) return;
      live.footprint = msg.polygon.points.map((p) => { const [x, y] = apply(T, p.x, p.y); return { x, y }; });
      mark();
    };
  }
  return (msg) => {
    const g = gridFromMsg(msg);
    if (g.frame && g.frame !== "map") {
      const T = live.tf.lookup("map", g.frame);
      if (!T) return;
      g.toMap = T;
    }
    live[key] = g;
    mark();
  };
}

export function useROS(enabled = true, live, sensors = [], url = WS_URL) {
  const [status, setStatus] = useState(enabled ? "connecting" : "off");
  const [retryAt, setRetryAt] = useState(null);
  const [missionState, setMissionState] = useState(DEFAULT_STATE);
  const [feedbackLog, setFeedbackLog] = useState([]);
  const [plan, setPlan] = useState([]);
  const [attempt, setAttempt] = useState(0);
  const [ros, setRos] = useState(null);
  const cmdPubRef = useRef(null);
  const failuresRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setStatus("off");
      setRetryAt(null);
      return undefined;
    }
    let disposed = false;
    let retryTimer = null;
    let planClear = null;
    let conn;

    // Only the first attempt reads as "connecting"; retries stay "offline"
    // so the header and notice don't flicker on every attempt.
    setStatus((s) => (s === "offline" ? "offline" : "connecting"));
    setRetryAt(null);

    const scheduleRetry = () => {
      if (disposed || retryTimer) return;
      const wait = BACKOFF[Math.min(failuresRef.current, BACKOFF.length - 1)];
      failuresRef.current += 1;
      setRetryAt(Date.now() + wait * 1000);
      retryTimer = setTimeout(() => setAttempt((a) => a + 1), wait * 1000);
    };

    try {
      conn = new ROSLIB.Ros({ url });
    } catch {
      setStatus("offline");
      scheduleRetry();
      return () => { disposed = true; clearTimeout(retryTimer); };
    }

    conn.on("connection", () => {
      if (disposed) return;
      failuresRef.current = 0;
      setStatus("connected");
      setRetryAt(null);
      setRos(conn);
    });
    conn.on("close", () => {
      if (disposed) return;
      setStatus("offline");
      setRos(null);
      cmdPubRef.current = null;
      live?.reset();
      scheduleRetry();
    });
    conn.on("error", () => {
      if (disposed) return;
      setStatus("offline");
    });

    const stateTopic = new ROSLIB.Topic({ ros: conn, name: "/mission_control/state", messageType: "std_msgs/msg/String" });
    stateTopic.subscribe((msg) => {
      try { setMissionState(normalizeState(JSON.parse(msg.data))); } catch { /* malformed frame */ }
    });

    const feedbackTopic = new ROSLIB.Topic({ ros: conn, name: "/mission_control/feedback", messageType: "std_msgs/msg/String" });
    feedbackTopic.subscribe((msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data?.message) setFeedbackLog((prev) => [...prev.slice(-200), data]);
      } catch { /* malformed frame */ }
    });

    const planTopic = new ROSLIB.Topic({
      ros: conn, name: "/plan", messageType: "nav_msgs/msg/Path", throttle_rate: 200, queue_length: 1,
    });
    planTopic.subscribe((msg) => {
      setPlan((msg.poses || []).map((p) => ({ x: p.pose.position.x, y: p.pose.position.y })));
      // Nav2 stops publishing /plan when idle; drop the stale path.
      clearTimeout(planClear);
      planClear = setTimeout(() => setPlan([]), 1500);
    });

    cmdPubRef.current = new ROSLIB.Topic({ ros: conn, name: "/mission_control/command", messageType: "std_msgs/msg/String" });

    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      clearTimeout(planClear);
      cmdPubRef.current = null;
      setRos(null);
      try {
        stateTopic.unsubscribe();
        feedbackTopic.unsubscribe();
        planTopic.unsubscribe();
        conn.close();
      } catch { /* already closed */ }
    };
  }, [enabled, attempt, live, url]);

  // Visualisation topics, on demand.
  const sensorKey = [...new Set(sensors)].sort().join(",");
  useEffect(() => {
    if (!ros || !live || !sensorKey) return undefined;
    const topics = sensorKey.split(",").filter((k) => SENSOR_TOPICS[k]).map((key) => {
      const spec = SENSOR_TOPICS[key];
      const topic = new ROSLIB.Topic({
        ros,
        name: spec.name,
        messageType: spec.type,
        compression: "cbor",
        throttle_rate: spec.throttle,
        queue_length: 1,
      });
      topic.subscribe(handlerFor(key, live));
      return { key, topic };
    });
    return () => topics.forEach(({ key, topic }) => {
      try { topic.unsubscribe(); } catch { /* connection gone */ }
      if (key === "globalCostmap" || key === "localCostmap" || key === "rackCloud" || key === "footprint") live[key] = null;
      if (key in SCAN_KEYS) live.scans[SCAN_KEYS[key]] = null;
    });
  }, [ros, live, sensorKey]);

  const sendCommand = useCallback((cmd) => {
    try {
      cmdPubRef.current?.publish({ data: JSON.stringify(cmd) });
    } catch (err) {
      console.warn("[ROS] Failed to send command:", err);
    }
  }, []);

  const retryNow = useCallback(() => {
    failuresRef.current = 0;
    setAttempt((a) => a + 1);
  }, []);

  return { status, retryAt, missionState, feedbackLog, plan, sendCommand, retryNow, url };
}
