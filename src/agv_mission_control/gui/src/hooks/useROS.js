/**
 * useROS — React hook for rosbridge WebSocket connection.
 *
 * Manages connection lifecycle, topic subscriptions/publishing,
 * and provides the full mission state to all components.
 */
import { useState, useEffect, useRef, useCallback } from "react";
import * as ROSLIB from "roslib";

const WS_URL = `ws://${window.location.hostname}:9090`;

export function useROS() {
  const [connected, setConnected] = useState(false);
  const [missionState, setMissionState] = useState({
    state: "IDLE",
    robot_pose: { x: 0, y: 0, yaw: 0 },
    current_task: null,
    current_step: "",
    progress: 0,
    queue: [],
    locations: { dock: {}, racks: [], drop_zones: [] },
  });
  const [feedbackLog, setFeedbackLog] = useState([]);
  const rosRef = useRef(null);
  const cmdPubRef = useRef(null);
  const teleopPubRef = useRef(null);

  useEffect(() => {
    const ros = new ROSLIB.Ros({ url: WS_URL });

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
        setMissionState(data);
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
        setFeedbackLog((prev) => [...prev.slice(-200), data]);
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

    // ── Publisher: teleop ────────────────────────────────────────
    teleopPubRef.current = new ROSLIB.Topic({
      ros,
      name: "/mission_control/command",
      messageType: "std_msgs/msg/String",
    });

    return () => {
      stateTopic.unsubscribe();
      feedbackTopic.unsubscribe();
      ros.close();
    };
  }, []);

  const sendCommand = useCallback((cmd) => {
    if (cmdPubRef.current) {
      cmdPubRef.current.publish(
        new ROSLIB.Message({ data: JSON.stringify(cmd) })
      );
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
