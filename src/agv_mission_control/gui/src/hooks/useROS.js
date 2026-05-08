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
  queue: [],
  locations: { dock: { x: 0, y: 0, yaw: 0, name: "Dock" }, racks: [], drop_zones: [] },
};

export function useROS() {
  const [connected, setConnected] = useState(false);
  const [missionState, setMissionState] = useState(DEFAULT_STATE);
  const [feedbackLog, setFeedbackLog] = useState([]);
  const rosRef = useRef(null);
  const cmdPubRef = useRef(null);

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
          // Merge with defaults so we never lose required fields
          setMissionState((prev) => ({
            ...DEFAULT_STATE,
            ...data,
            robot_pose: { ...DEFAULT_STATE.robot_pose, ...(data.robot_pose || {}) },
            locations: {
              dock: { ...DEFAULT_STATE.locations.dock, ...(data.locations?.dock || {}) },
              racks: Array.isArray(data.locations?.racks) ? data.locations.racks : [],
              drop_zones: Array.isArray(data.locations?.drop_zones) ? data.locations.drop_zones : [],
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
    sendCommand,
    ros: rosRef,
  };
}
