/**
 * useROS — React hook for rosbridge WebSocket connection.
 *
 * Manages connection lifecycle, topic subscriptions/publishing,
 * and provides the full mission state to all components.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import * as ROSLIB from "roslib";

const WS_URL = `ws://${window.location.hostname}:9090`;

const DEFAULT_STATE = {
  state: "IDLE",
  robot_pose: { x: 0, y: 0, yaw: 0 },
  current_task: null,
  current_step: "",
  progress: 0,
  carrying: false,
  speed: 0.4,
  queue: [],
  locations: { dock: { x: 0, y: 0, yaw: 0, name: "Dock" }, racks: [], drop_zones: [], lanes: [] },
};

export function useROS() {
  const [connected, setConnected] = useState(false);
  const [missionState, setMissionState] = useState(DEFAULT_STATE);
  const [feedbackLog, setFeedbackLog] = useState([]);
  const [plan, setPlan] = useState([]);
  const rosRef = useRef(null);
  const cmdPubRef = useRef(null);
  const planClearRef = useRef(null);

  useEffect(() => {
    let ros;
    try {
      ros = new ROSLIB.Ros({ url: WS_URL });
    } catch (err) {
      console.error("[ROS] Failed to create connection:", err);
      return;
    }

    ros.on("connection", () => {
      console.log("[ROS] Connected to", WS_URL);
      setConnected(true);
    });

    ros.on("close", () => {
      console.log("[ROS] Disconnected");
      setConnected(false);
    });

    ros.on("error", (err) => {
      console.error("[ROS] Error:", err);
    });

    rosRef.current = ros;

    // ── Subscribe to state ───────────────────────────────────────
    const stateTopic = new ROSLIB.Topic({
      ros,
      name: "/mission_control/state",
      messageType: "std_msgs/msg/String",
    });
    stateTopic.subscribe((msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data && typeof data === "object") {
          // Merge with defaults so we never lose required fields.
          // NOTE: lanes MUST be carried through here — they were being
          // dropped, which is why drawn lanes never re-appeared.
          setMissionState(() => ({
            ...DEFAULT_STATE,
            ...data,
            robot_pose: { ...DEFAULT_STATE.robot_pose, ...(data.robot_pose || {}) },
            locations: {
              dock: { ...DEFAULT_STATE.locations.dock, ...(data.locations?.dock || {}) },
              racks: Array.isArray(data.locations?.racks) ? data.locations.racks : [],
              drop_zones: Array.isArray(data.locations?.drop_zones) ? data.locations.drop_zones : [],
              lanes: Array.isArray(data.locations?.lanes) ? data.locations.lanes : [],
            },
          }));
        }
      } catch (e) {
        /* ignore parse errors */
      }
    });

    // ── Subscribe to feedback ────────────────────────────────────
    const feedbackTopic = new ROSLIB.Topic({
      ros,
      name: "/mission_control/feedback",
      messageType: "std_msgs/msg/String",
    });
    feedbackTopic.subscribe((msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data && data.message) {
          setFeedbackLog((prev) => [...prev.slice(-200), data]);
        }
      } catch (e) {
        /* ignore */
      }
    });

    // ── Subscribe to the global plan (Nav2) ──────────────────────
    const planTopic = new ROSLIB.Topic({
      ros,
      name: "/plan",
      messageType: "nav_msgs/msg/Path",
      throttle_rate: 200,
      queue_length: 1,
    });
    planTopic.subscribe((msg) => {
      const pts = (msg.poses || []).map((p) => ({
        x: p.pose.position.x,
        y: p.pose.position.y,
      }));
      setPlan(pts);
      // Auto-clear: Nav2 stops publishing /plan when idle, so drop the
      // stale path ~1.5 s after the last update.
      if (planClearRef.current) clearTimeout(planClearRef.current);
      planClearRef.current = setTimeout(() => setPlan([]), 1500);
    });

    // ── Publisher: commands ───────────────────────────────────────
    cmdPubRef.current = new ROSLIB.Topic({
      ros,
      name: "/mission_control/command",
      messageType: "std_msgs/msg/String",
    });

    return () => {
      try {
        stateTopic.unsubscribe();
        feedbackTopic.unsubscribe();
        planTopic.unsubscribe();
        if (planClearRef.current) clearTimeout(planClearRef.current);
        ros.close();
      } catch (e) {
        /* ignore cleanup errors */
      }
    };
  }, []);

  const sendCommand = useCallback((cmd) => {
    try {
      if (cmdPubRef.current) {
        cmdPubRef.current.publish({ data: JSON.stringify(cmd) });
      }
    } catch (err) {
      console.warn("[ROS] Failed to send command:", err);
    }
  }, []);

  return {
    connected,
    missionState,
    feedbackLog,
    plan,
    sendCommand,
    ros: rosRef,
  };
}
