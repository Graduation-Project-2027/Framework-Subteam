"""
AGV Mission Control — Mission Manager Node

Manages a task queue of rack-transport missions, drives Nav2 goals,
controls the lift mechanism, and relays state/feedback to the web GUI
via rosbridge topics.

Topics Published:
  /mission_control/state     (std_msgs/String)  JSON state blob
  /mission_control/feedback  (std_msgs/String)  Human-readable log lines

Topics Subscribed:
  /mission_control/command   (std_msgs/String)  JSON commands from GUI
  /odom                      (nav_msgs/Odometry) Robot pose

Actions Used:
  /navigate_to_pose          (nav2_msgs/NavigateToPose)
"""

import json
import os
import math
import time
import uuid
from enum import Enum
from threading import Lock

import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy

import struct

from std_msgs.msg import String, Float64, Header
from geometry_msgs.msg import Twist, PoseStamped
from sensor_msgs.msg import LaserScan, PointCloud2, PointField
from nav2_msgs.action import NavigateToPose
from rcl_interfaces.srv import SetParameters
from rcl_interfaces.msg import Parameter as RclParameter
from rcl_interfaces.msg import ParameterValue, ParameterType

from tf2_ros import Buffer, TransformListener


def _quaternion_to_yaw(x, y, z, w):
    """Extract yaw from a quaternion (inline, no tf_transformations dependency)."""
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


# ── State Machine ────────────────────────────────────────────────────
class MissionState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    NAVIGATING = "NAVIGATING"
    LIFTING = "LIFTING"
    DROPPING = "DROPPING"
    RETURNING = "RETURNING"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


class MissionManagerNode(Node):
    def __init__(self):
        super().__init__("mission_manager")

        self.cb_group = ReentrantCallbackGroup()

        # ── Declare all parameters ────────────────────────────────────
        self._declare_params()

        # ── State ────────────────────────────────────────────────────
        self._state = MissionState.IDLE
        self._task_queue = []          # list of task dicts
        self._current_task = None      # currently executing task
        self._current_task_step = ""   # "navigating_to_rack" / "lifting" / etc.
        self._progress = 0.0           # 0.0 – 1.0
        self._robot_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._speed_multiplier = self.p_default_speed
        self._carrying = False  # True when rack is lifted
        self._latest_scans = {}  # {topic: LaserScan}
        self._lock = Lock()
        self._paused = False
        self._cancel_requested = False
        self._nav_goal_handle = None

        # ── Locations ────────────────────────────────────────────────
        # Persist to ~/.ros/agv_locations.json by default — survives
        # colcon rebuilds (the install/.../config/locations.json copy
        # gets clobbered every build, wiping any GUI edits).
        param_path = self.declare_parameter(
            "locations_file", ""
        ).get_parameter_value().string_value

        persistent_path = os.path.expanduser("~/.ros/agv_locations.json")
        if param_path:
            self._locations_file = param_path
        else:
            self._locations_file = persistent_path
            # Seed the persistent file from the package share copy the
            # FIRST TIME the system runs, so we don't start blank.
            if not os.path.exists(persistent_path):
                from ament_index_python.packages import get_package_share_directory
                try:
                    pkg = get_package_share_directory("agv_mission_control")
                    seed = os.path.join(pkg, "config", "locations.json")
                    if os.path.exists(seed):
                        os.makedirs(os.path.dirname(persistent_path), exist_ok=True)
                        import shutil
                        shutil.copy(seed, persistent_path)
                        self.get_logger().info(
                            f"Seeded persistent locations from {seed}")
                except Exception as e:
                    self.get_logger().warn(f"Could not seed locations: {e}")

        self.get_logger().info(f"Using locations file: {self._locations_file}")
        self._locations = self._load_locations()

        # ── Publishers ───────────────────────────────────────────────
        self._state_pub = self.create_publisher(String, "/mission_control/state", 10)
        self._feedback_pub = self.create_publisher(String, "/mission_control/feedback", 10)

        # Rack obstacles — leg points for occupied racks/zones.
        # Nav2's obstacle layer subscribes to this and treats each point
        # as a lethal cell.  Latching QoS so newly-started costmaps see
        # the current state without waiting for the next periodic publish.
        rack_obs_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._rack_obs_pub = self.create_publisher(
            PointCloud2, "/rack_obstacles", rack_obs_qos)

        # ── TF for robot pose (map frame) ────────────────────────────
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)

        # ── Subscribers ──────────────────────────────────────────────
        self.create_subscription(
            String, "/mission_control/command", self._command_cb, 10,
            callback_group=self.cb_group,
        )

        # ── Lift publisher ───────────────────────────────────────────
        self._lift_pub = self.create_publisher(Float64, "/lift_joint/cmd_pos", 10)

        # ── Teleop publisher (relayed from GUI) ──────────────────────
        self._cmd_vel_pub = self.create_publisher(Twist, "/cmd_vel", 10)

        # ── Lidar subscriptions (for rack-leg detection) ─────────────
        scan_qos = QoSProfile(
            depth=5,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        for i, topic in enumerate(self.p_scan_topics):
            tag = f"scan_{i}"
            self.create_subscription(
                LaserScan, topic,
                lambda msg, t=tag: self._scan_cb(t, msg),
                scan_qos, callback_group=self.cb_group,
            )
            self.get_logger().info(f"Subscribed to {topic} as '{tag}'")

        # ── Costmap parameter clients (for dynamic footprint switch) ──
        self._local_costmap_param_cli = self.create_client(
            SetParameters,
            '/local_costmap/local_costmap/set_parameters',
            callback_group=self.cb_group,
        )
        self._global_costmap_param_cli = self.create_client(
            SetParameters,
            '/global_costmap/global_costmap/set_parameters',
            callback_group=self.cb_group,
        )

        # ── Nav2 action client ───────────────────────────────────────
        self._nav_client = ActionClient(
            self, NavigateToPose, "navigate_to_pose",
            callback_group=self.cb_group,
        )

        # ── Periodic state broadcast (5 Hz) ──────────────────────────
        self.create_timer(0.2, self._publish_state)

        # ── Periodic rack-obstacles broadcast (3 Hz) ─────────────────
        self.create_timer(0.33, self._publish_rack_obstacles)

        self._log("Mission Manager started. Waiting for commands.")
        self.get_logger().info("Mission Manager Node initialized.")

    def _declare_params(self):
        """Declare all ROS 2 parameters with defaults."""
        g = lambda n, d: self.declare_parameter(n, d).get_parameter_value()

        # Footprints
        self.p_footprint_robot = g(
            "footprint_robot",
            "[[0.35, 0.32], [0.35, -0.32], [-0.35, -0.32], [-0.35, 0.32]]"
        ).string_value
        self.p_footprint_carrying = g(
            "footprint_carrying",
            "[[0.525, 0.525], [0.525, -0.525], [-0.525, -0.525], [-0.525, 0.525]]"
        ).string_value

        # Scan topics
        self.p_scan_topics = g(
            "scan_topics",
            ["/scan_front_filtered", "/scan_rear_filtered"]
        ).string_array_value

        # Rack geometry
        self.p_rack_side      = g("rack_side_length", 0.95).double_value
        self.p_rack_side_min  = g("rack_side_min", 0.60).double_value
        self.p_rack_side_max  = g("rack_side_max", 1.30).double_value
        self.p_rack_diag_min  = g("rack_diag_min", 1.00).double_value
        self.p_rack_diag_max  = g("rack_diag_max", 1.70).double_value

        # Rack-leg detection
        self.p_leg_range_min  = g("leg_detect_range_min", 0.15).double_value
        self.p_leg_range_max  = g("leg_detect_range_max", 0.85).double_value
        self.p_leg_dist_min   = g("leg_detect_dist_min", 0.15).double_value
        self.p_leg_dist_max   = g("leg_detect_dist_max", 0.75).double_value
        self.p_leg_cluster_d  = g("leg_cluster_max_dist", 0.12).double_value

        # Centering
        self.p_center_tol     = g("centering_tolerance", 0.05).double_value
        self.p_center_speed   = g("centering_speed", 0.08).double_value
        self.p_center_iters   = g("centering_max_iterations", 200).integer_value
        self.p_center_fail_n  = g("centering_detection_timeout", 30).integer_value

        # Transport timing
        self.p_lift_height    = g("lift_height", 1.0).double_value
        self.p_lift_wait      = g("lift_wait_time", 3.0).double_value
        self.p_settle_time    = g("costmap_settle_time", 1.0).double_value
        self.p_drop_wait      = g("drop_wait_time", 2.0).double_value

        # Speed
        self.p_default_speed  = g("default_speed", 0.4).double_value
        self.p_teleop_lin     = g("teleop_linear_scale", 0.4).double_value
        self.p_teleop_ang     = g("teleop_angular_scale", 0.8).double_value

        # Navigation
        self.p_nav_timeout    = g("nav_goal_timeout", 10.0).double_value
        self.p_nav_poll       = g("nav_poll_rate", 0.05).double_value

        # Centering rotation (sweep lidars when detection stalls)
        self.p_center_rotate_after = g("centering_rotate_after_fails", 8).integer_value
        self.p_center_rotate_speed = g("centering_rotate_speed", 0.3).double_value

        # Crawl distances (straight-line moves without Nav2)
        self.p_crawl_speed      = g("crawl_speed", 0.12).double_value
        # Pickup: short staging hop just past the rack's near legs,
        #         keeps the staging point in the aisle.
        self.p_pickup_approach  = g("pickup_approach_distance", 0.55).double_value
        # Carrying: longer because the bigger footprint needs more
        #           clearance from static-map rack legs after extraction.
        self.p_carrying_approach = g("carrying_approach_distance", 1.20).double_value

        # Inflation radius profiles.  We use three values:
        #   - normal: default safe inflation for general navigation
        #   - pickup: medium inflation during empty-footprint approach.
        #             Higher than carrying so the planner pulls paths to
        #             the centerline of rack rows (avoids clipping legs).
        #   - carrying: low inflation so the larger carrying footprint can
        #               still find paths through the warehouse.
        self.p_inflation_normal   = g("inflation_radius_normal", 0.55).double_value
        self.p_inflation_pickup   = g("inflation_radius_pickup", 0.40).double_value
        self.p_inflation_carrying = g("inflation_radius_carrying", 0.25).double_value

        self.get_logger().info("Parameters loaded.")

    # ─────────────────────────────────────────────────────────────────
    # Location persistence
    # ─────────────────────────────────────────────────────────────────
    def _load_locations(self):
        try:
            with open(self._locations_file, "r") as f:
                data = json.load(f)
            self.get_logger().info(f"Loaded locations from {self._locations_file}")
            # Backfill missing keys for older files
            data.setdefault("racks", [])
            data.setdefault("drop_zones", [])
            data.setdefault("lanes", [])
            return data
        except Exception as e:
            self.get_logger().warn(f"Could not load locations: {e}")
            return {"dock": {"x": 0, "y": 0, "yaw": 0, "name": "Dock"},
                    "racks": [], "drop_zones": [], "lanes": []}

    def _save_locations(self):
        try:
            with open(self._locations_file, "w") as f:
                json.dump(self._locations, f, indent=2)
            self._log("Locations saved.")
        except Exception as e:
            self._log(f"ERROR: Could not save locations: {e}")

    # ─────────────────────────────────────────────────────────────────
    # Callbacks
    # ─────────────────────────────────────────────────────────────────
    def _update_robot_pose(self):
        """Look up robot pose in the map frame via TF."""
        try:
            t = self._tf_buffer.lookup_transform(
                "map", "base_footprint", rclpy.time.Time())
            p = t.transform.translation
            q = t.transform.rotation
            yaw = _quaternion_to_yaw(q.x, q.y, q.z, q.w)
            self._robot_pose = {"x": p.x, "y": p.y, "yaw": yaw}
        except Exception:
            pass  # keep last known pose

    def _command_cb(self, msg: String):
        """Handle JSON commands from the GUI."""
        try:
            cmd = json.loads(msg.data)
        except json.JSONDecodeError:
            self._log("ERROR: Invalid JSON command received.")
            return

        action = cmd.get("action", "")
        self.get_logger().info(f"Command received: {action}")

        if action == "add_task":
            self._handle_add_task(cmd)
        elif action == "remove_task":
            self._handle_remove_task(cmd)
        elif action == "reorder_tasks":
            self._handle_reorder(cmd)
        elif action == "start":
            self._handle_start()
        elif action == "pause":
            self._handle_pause()
        elif action == "resume":
            self._handle_resume()
        elif action == "stop":
            self._handle_stop()
        elif action == "estop":
            self._handle_estop()
        elif action == "return_to_dock":
            self._handle_return_to_dock()
        elif action == "update_locations":
            self._handle_update_locations(cmd)
        elif action == "update_dock":
            self._handle_update_dock(cmd)
        elif action == "teleop":
            self._handle_teleop(cmd)
        elif action == "lift":
            self._handle_lift(cmd)
        elif action == "set_speed":
            self._handle_set_speed(cmd)
        elif action == "navigate_to":
            self._handle_navigate_to(cmd)
        elif action == "set_location_state":
            self._set_location_state(cmd.get("id", ""), cmd.get("state", ""))
        elif action == "update_lanes":
            self._handle_update_lanes(cmd)
        else:
            self._log(f"Unknown command: {action}")

    # ─────────────────────────────────────────────────────────────────
    # Command handlers
    # ─────────────────────────────────────────────────────────────────
    def _get_location_state(self, loc_id):
        """Return current state ('free'/'occupied') for a rack or zone id."""
        if not loc_id:
            return None
        for key in ("racks", "drop_zones"):
            for loc in self._locations.get(key, []):
                if loc.get("id") == loc_id:
                    return loc.get("state")
        return None

    def _handle_add_task(self, cmd):
        # ── Pre-add validation for transport tasks ────────────────────
        # Source must be OCCUPIED (something to pick up) and destination
        # must be FREE (room for a rack).  Reject otherwise so the user
        # doesn't queue physically impossible tasks.
        if cmd.get("type", "transport") == "transport":
            src_id = (cmd.get("from") or {}).get("id")
            dst_id = (cmd.get("to") or {}).get("id")
            src_state = self._get_location_state(src_id)
            dst_state = self._get_location_state(dst_id)

            if src_id and src_state == "free":
                self._log(f"ERROR: Cannot add task — source "
                          f"'{cmd.get('from_name', src_id)}' is FREE "
                          f"(no rack to pick up).")
                return
            if dst_id and dst_state == "occupied":
                self._log(f"ERROR: Cannot add task — destination "
                          f"'{cmd.get('to_name', dst_id)}' is OCCUPIED "
                          f"(would crash into an existing rack).")
                return

        task = {
            "id": cmd.get("id", str(uuid.uuid4())[:8]),
            "type": cmd.get("type", "transport"),  # transport | navigate
            "rack_id": cmd.get("rack_id", ""),
            "from_name": cmd.get("from_name", ""),
            "to_name": cmd.get("to_name", ""),
            "from": cmd.get("from", {}),
            "to": cmd.get("to", {}),
            "status": "queued",
        }
        with self._lock:
            self._task_queue.append(task)
        self._log(f"Task added: {task['from_name']} → {task['to_name']}")

    def _handle_remove_task(self, cmd):
        task_id = cmd.get("id", "")
        with self._lock:
            self._task_queue = [t for t in self._task_queue if t["id"] != task_id]
        self._log(f"Task removed: {task_id}")

    def _handle_reorder(self, cmd):
        order = cmd.get("order", [])
        with self._lock:
            id_map = {t["id"]: t for t in self._task_queue}
            self._task_queue = [id_map[tid] for tid in order if tid in id_map]
        self._log("Task queue reordered.")

    def _handle_start(self):
        if self._state != MissionState.IDLE:
            self._log("Cannot start: not in IDLE state.")
            return
        if not self._task_queue:
            self._log("Cannot start: task queue is empty.")
            return
        self._state = MissionState.RUNNING
        self._cancel_requested = False
        self._paused = False
        self._log("Mission queue started.")
        # Execute in a separate thread so the node keeps spinning
        import threading
        threading.Thread(target=self._execute_queue, daemon=True).start()

    def _handle_pause(self):
        if self._state in (MissionState.RUNNING, MissionState.NAVIGATING,
                           MissionState.LIFTING, MissionState.DROPPING):
            self._paused = True
            self._state = MissionState.PAUSED
            # Cancel current nav goal if navigating
            if self._nav_goal_handle is not None:
                self._nav_goal_handle.cancel_goal_async()
            self._log("Mission paused.")

    def _handle_resume(self):
        if self._state == MissionState.PAUSED:
            self._paused = False
            self._state = MissionState.RUNNING
            self._log("Mission resumed.")
            import threading
            threading.Thread(target=self._execute_queue, daemon=True).start()

    def _handle_stop(self):
        self._cancel_requested = True
        self._paused = False
        if self._nav_goal_handle is not None:
            self._nav_goal_handle.cancel_goal_async()
        self._state = MissionState.IDLE
        self._current_task = None
        self._log("Mission stopped.")

    def _handle_estop(self):
        self._cancel_requested = True
        self._paused = False
        # Stop all movement immediately
        self._cmd_vel_pub.publish(Twist())
        if self._nav_goal_handle is not None:
            self._nav_goal_handle.cancel_goal_async()
        self._state = MissionState.ERROR
        self._log("⚠ EMERGENCY STOP ACTIVATED ⚠")

    def _handle_return_to_dock(self):
        dock = self._locations.get("dock", {})
        if not dock:
            self._log("No dock position configured.")
            return
        task = {
            "id": "return_dock",
            "type": "navigate",
            "from_name": "Current",
            "to_name": dock.get("name", "Dock"),
            "to": {"x": dock["x"], "y": dock["y"], "yaw": dock.get("yaw", 0.0)},
            "status": "queued",
        }
        with self._lock:
            self._task_queue.insert(0, task)
        self._log(f"Return to dock queued.")
        if self._state == MissionState.IDLE:
            self._handle_start()

    def _handle_update_locations(self, cmd):
        # Preserve runtime 'state' across GUI re-syncs.  Without this, every
        # location-list edit from the GUI (renaming, etc.) would wipe out
        # which racks are currently free/occupied.
        prev_state = {}
        for key in ("racks", "drop_zones"):
            for loc in self._locations.get(key, []):
                if loc.get("id"):
                    prev_state[loc["id"]] = loc.get("state")

        def restore_state(items, default):
            for it in items:
                if "state" not in it:
                    it["state"] = prev_state.get(it.get("id"), default)
            return items

        self._locations["racks"] = restore_state(
            cmd.get("racks", []), default="occupied")
        self._locations["drop_zones"] = restore_state(
            cmd.get("drop_zones", []), default="free")
        self._save_locations()
        self._publish_rack_obstacles()

    def _handle_update_dock(self, cmd):
        self._locations["dock"] = {
            "x": cmd.get("x", 0.0),
            "y": cmd.get("y", 0.0),
            "yaw": cmd.get("yaw", 0.0),
            "name": cmd.get("name", "Dock"),
        }
        self._save_locations()
        self._log(f"Dock updated to ({cmd.get('x')}, {cmd.get('y')})")

    def _handle_update_lanes(self, cmd):
        """Replace the lane set with the GUI's authoritative copy.

        Each lane is { id, name, points: [{x, y}, ...] }.  Lanes with
        fewer than 2 points are dropped — they wouldn't form an edge.
        """
        lanes_in = cmd.get("lanes", [])
        cleaned = []
        for lane in lanes_in:
            pts = [
                {"x": float(p["x"]), "y": float(p["y"])}
                for p in lane.get("points", [])
                if "x" in p and "y" in p
            ]
            if len(pts) < 2:
                continue
            cleaned.append({
                "id": lane.get("id") or f"lane_{int(time.time() * 1000)}",
                "name": lane.get("name", ""),
                "points": pts,
            })
        self._locations["lanes"] = cleaned
        self._save_locations()
        self._log(f"Lanes updated: {len(cleaned)} polyline(s).")

    def _handle_teleop(self, cmd):
        twist = Twist()
        lx = float(cmd.get("lx", 0.0))
        ly = float(cmd.get("ly", 0.0))
        az = float(cmd.get("az", 0.0))
        # Scale by speed multiplier (GUI sends ±1 relative values)
        # The speed values from the GUI are already in the 0.4 range,
        # so we scale by multiplier / 0.4
        scale = self._speed_multiplier / self.p_teleop_lin
        twist.linear.x = lx * scale
        twist.linear.y = ly * scale
        twist.angular.z = az * scale
        self._cmd_vel_pub.publish(twist)

    def _handle_set_speed(self, cmd):
        speed = float(cmd.get("speed", 0.4))
        speed = max(0.1, min(1.0, speed))
        self._speed_multiplier = speed
        self._log(f"Max speed set to {speed:.2f} m/s")

    def _handle_lift(self, cmd):
        height = float(cmd.get("height", 0.0))
        msg = Float64()
        msg.data = height
        self._lift_pub.publish(msg)
        self._log(f"Lift commanded to {height:.2f}m")

    def _handle_navigate_to(self, cmd):
        """Direct click-to-navigate from the map."""
        x = float(cmd.get("x", 0.0))
        y = float(cmd.get("y", 0.0))
        yaw = float(cmd.get("yaw", 0.0))
        task = {
            "id": str(uuid.uuid4())[:8],
            "type": "navigate",
            "from_name": "Current",
            "to_name": cmd.get("name", f"({x:.1f}, {y:.1f})"),
            "to": {"x": x, "y": y, "yaw": yaw},
            "status": "queued",
        }
        with self._lock:
            self._task_queue.insert(0, task)
        if self._state == MissionState.IDLE:
            self._handle_start()

    # ─────────────────────────────────────────────────────────────────
    # Mission execution
    # ─────────────────────────────────────────────────────────────────
    def _execute_queue(self):
        """Sequentially execute tasks from the queue."""
        while True:
            if self._cancel_requested or self._paused:
                return

            with self._lock:
                if not self._task_queue:
                    break
                task = self._task_queue[0]

            self._current_task = task
            task["status"] = "running"

            if task["type"] == "transport":
                success = self._execute_transport(task)
            elif task["type"] == "navigate":
                success = self._execute_navigate(task)
            else:
                self._log(f"Unknown task type: {task['type']}")
                success = False

            if self._cancel_requested or self._paused:
                return

            with self._lock:
                if self._task_queue and self._task_queue[0]["id"] == task["id"]:
                    self._task_queue.pop(0)

            task["status"] = "done" if success else "failed"

            if not success:
                self._state = MissionState.ERROR
                self._log(f"Task failed: {task.get('to_name', task['id'])}")
                return

        # All tasks done — return to dock
        self._current_task = None
        dock = self._locations.get("dock", {})
        if dock and dock.get("x") is not None:
            self._log("All tasks complete. Returning to dock...")
            self._state = MissionState.RETURNING
            self._navigate_to(dock["x"], dock["y"], dock.get("yaw", 0.0))

        self._state = MissionState.IDLE
        self._log("Mission queue complete. Robot is idle.")

    # ─────────────────────────────────────────────────────────────────
    # Staging helpers
    # ─────────────────────────────────────────────────────────────────
    def _staging_point(self, x, y, yaw, distance):
        """Return the aisle staging point for a rack/drop position.

        Sits *distance* metres behind the position along the approach
        direction defined by *yaw*.  Caller chooses *distance* based on
        which footprint the staging is for (smaller for empty pickup,
        larger for carrying).
        """
        return (x - distance * math.cos(yaw), y - distance * math.sin(yaw))

    def _execute_transport(self, task):
        """Transport: stage→crawl-in→center→lift→reverse-out → nav-stage→crawl-in→drop→reverse-out.

        Reduced inflation is applied for the whole task so the planner can
        enter rack rows; restored on every exit path via try/finally.
        Pickup uses a SHORT staging hop (aisle edge); carrying uses a
        LONGER staging/extract distance (clears the bigger footprint).
        """
        src = task.get("from", {})
        dst = task.get("to", {})
        spd = self.p_crawl_speed
        pickup_d = self.p_pickup_approach
        carry_d  = self.p_carrying_approach

        # ── Runtime state re-check (queued tasks may have gone stale) ─
        if src.get("id") and self._get_location_state(src["id"]) == "free":
            self._log(f"ERROR: Source '{src.get('name', src['id'])}' is now "
                      f"FREE — rack already moved. Aborting task.")
            return False
        if dst.get("id") and self._get_location_state(dst["id"]) == "occupied":
            self._log(f"ERROR: Destination '{dst.get('name', dst['id'])}' is "
                      f"now OCCUPIED — would collide with an existing rack. "
                      f"Aborting task.")
            return False

        # ── Tune inflation for the pickup approach ───────────────────
        # 0.40m is high enough that paths get pulled to the centerline
        # of rack rows but low enough that rack rows aren't walled off.
        # _set_carrying_mode(True) lowers to p_inflation_carrying for
        # navigating with the bigger footprint after lift.
        self._set_inflation(self.p_inflation_pickup)
        time.sleep(self.p_settle_time)

        try:
            # ── Step 1: Navigate to PICKUP staging (aisle, short hop) ─
            # Forces the approach to come from the rack's aisle side
            # rather than wherever Nav2 finds cheapest.  The staging
            # point sits just past the near rack legs (~0.55 m), so
            # it lands in the aisle, not inside a neighbouring rack.
            self._current_task_step = "navigating_to_rack"
            self._state = MissionState.NAVIGATING
            if src:
                src_yaw = src.get("yaw", 0.0)
                stg_x, stg_y = self._staging_point(
                    src["x"], src["y"], src_yaw, pickup_d)

                if not self._route_via_lanes(stg_x, stg_y, src_yaw,
                                             label="to pickup"):
                    return False
                if self._cancel_requested or self._paused:
                    return False

                self._log(f"Navigating to pickup staging for "
                          f"{task.get('from_name', 'rack')}...")
                ok = self._navigate_to(stg_x, stg_y, src_yaw)
                if not ok:
                    return False

            if self._cancel_requested or self._paused:
                return False

            # ── Step 1b: Crawl forward into the rack slot ────────────
            # Closed-loop: read the robot's actual pose and compute the
            # forward distance to the rack centre instead of crawling a
            # fixed amount.  Nav2's xy_goal_tolerance (0.25 m) can leave
            # the robot well short of the staging point, and a fixed
            # crawl would inherit that error.
            if src:
                self._update_robot_pose()
                rx = self._robot_pose["x"]
                ry = self._robot_pose["y"]
                yaw = self._robot_pose["yaw"]
                dx = src["x"] - rx
                dy = src["y"] - ry
                forward_dist = dx * math.cos(yaw) + dy * math.sin(yaw)
                lateral_off  = -dx * math.sin(yaw) + dy * math.cos(yaw)
                # Cap at a safe upper bound — if Nav2 ended up way off
                # we don't want to barrel through obstacles.  Centering
                # will close any remaining gap.
                max_crawl = pickup_d + 1.0
                crawl_d = max(0.0, min(forward_dist, max_crawl))
                self._log(
                    f"  Distance to rack centre: forward {forward_dist:.2f} m, "
                    f"lateral {lateral_off:.2f} m → crawling {crawl_d:.2f} m")
                if crawl_d > 0.03:
                    self._crawl(spd, 0.0, crawl_d,
                                label="Crawling into rack slot")
                else:
                    self._log("  Skipping crawl — robot is already at/past rack centre.")

            if self._cancel_requested or self._paused:
                return False

            # ── Step 1c: Precision centering under rack ──────────────
            if src:
                self._update_robot_pose()
                rp = self._robot_pose
                self._log(
                    f"Centering under rack... "
                    f"(robot at ({rp['x']:.2f}, {rp['y']:.2f}, "
                    f"yaw={rp['yaw']:.2f}), target rack at "
                    f"({src['x']:.2f}, {src['y']:.2f}))")
                self._current_task_step = "centering"
                if not self._center_under_rack(src["x"], src["y"]):
                    self._log("ERROR: Centering failed — aborting task "
                              "to avoid lifting without a rack underneath.")
                    return False

            # ── Step 2: Lift ─────────────────────────────────────────
            self._current_task_step = "lifting"
            self._state = MissionState.LIFTING
            self._log("Lifting rack...")
            self._lift_to(self.p_lift_height)
            time.sleep(self.p_lift_wait)

            # Mark pickup location as FREE — the rack is no longer here.
            if src and src.get("id"):
                self._set_location_state(src["id"], "free")

            # Switch to carrying footprint
            self._set_carrying_mode(True)
            time.sleep(self.p_settle_time)

            # ── Step 2b: Extract — reverse ALL THE WAY to the lane foot ─
            # In one pure straight-line move with no rotation, so the
            # carried rack doesn't swing into neighbouring racks.
            # Distance is computed dynamically: from the robot's actual
            # pose backwards to the perpendicular foot on the source
            # lane.  This replaces the previous "fixed reverse + lane
            # waypoint" sequence that caused rack-rotation collisions.
            src_yaw = src.get("yaw", 0.0) if src else 0.0
            src_foot = None
            if src:
                src_foot = self._find_lane_approach_foot(
                    src["x"], src["y"], src_yaw)

            self._update_robot_pose()
            rx, ry = self._robot_pose["x"], self._robot_pose["y"]
            yaw = self._robot_pose["yaw"]

            if src_foot is not None:
                foot_x, foot_y = src_foot[0], src_foot[1]
                # Forward distance to the foot, in robot frame
                fwd_to_foot = ((foot_x - rx) * math.cos(yaw)
                               + (foot_y - ry) * math.sin(yaw))
                # Foot is on the approach side ⇒ it's BEHIND the robot
                # right now (negative forward distance).  Reverse exactly
                # that magnitude.
                rev_dist = -fwd_to_foot
                if rev_dist > 0.05:
                    self._log(
                        f"  Reversing {rev_dist:.2f} m straight back to "
                        f"source aisle foot ({foot_x:.2f}, {foot_y:.2f}).")
                    self._crawl(-spd, 0.0, rev_dist,
                                label="Reversing rack to aisle")
                else:
                    self._log(
                        f"  Source foot is not behind robot "
                        f"(fwd={fwd_to_foot:.2f} m) — using fixed "
                        f"{carry_d:.2f} m fallback.")
                    self._crawl(-spd, 0.0, carry_d,
                                label="Reversing rack (fallback)")
            elif carry_d > 0:
                # No lanes defined: fall back to the fixed reverse distance
                self._crawl(-spd, 0.0, carry_d,
                            label="Reversing rack to aisle")
            time.sleep(0.3)
            self._clear_costmaps()
            time.sleep(0.5)

            if self._cancel_requested or self._paused:
                return False

            # ── Step 3: Navigate to DROP staging point (in the aisle) ─
            # If lanes are defined, follow them through the warehouse
            # rather than letting Nav2 free-plan through rack rows.
            # The robot snaps to the nearest lane node, traverses the
            # lane graph to the node nearest the drop staging, then
            # makes a final hop to the staging point itself.
            self._current_task_step = "navigating_to_dest"
            self._state = MissionState.NAVIGATING
            if dst:
                dst_yaw = dst.get("yaw", 0.0)
                stg_x, stg_y = self._staging_point(
                    dst["x"], dst["y"], dst_yaw, carry_d)

                if not self._route_via_lanes(stg_x, stg_y, dst_yaw,
                                             label="to drop"):
                    return False
                if self._cancel_requested or self._paused:
                    return False

                self._log(f"Navigating to drop staging for "
                          f"{task.get('to_name', 'destination')}...")
                ok = self._navigate_to(stg_x, stg_y, dst_yaw)
                if not ok:
                    return False

            if self._cancel_requested or self._paused:
                return False

            # ── Step 3b: Insert — closed-loop crawl into drop slot ───
            # Compute the actual forward distance from current pose to
            # the drop centre (Nav2 may have left us short of staging).
            if dst:
                self._update_robot_pose()
                rx = self._robot_pose["x"]
                ry = self._robot_pose["y"]
                yaw = self._robot_pose["yaw"]
                dx = dst["x"] - rx
                dy = dst["y"] - ry
                forward_dist = dx * math.cos(yaw) + dy * math.sin(yaw)
                max_crawl = carry_d + 1.0
                crawl_d = max(0.0, min(forward_dist, max_crawl))
                self._log(f"  Forward to drop centre: {forward_dist:.2f} m "
                          f"→ crawling {crawl_d:.2f} m.")
                if crawl_d > 0.03:
                    self._crawl(spd, 0.0, crawl_d,
                                label="Crawling into drop position")

            if self._cancel_requested or self._paused:
                return False

            # ── Step 4: Drop ─────────────────────────────────────────
            self._current_task_step = "dropping"
            self._state = MissionState.DROPPING
            self._log("Dropping rack...")
            self._lift_to(0.0)
            time.sleep(self.p_drop_wait)

            # Mark drop location as OCCUPIED — there's a rack there now.
            # The next /rack_obstacles publish makes Nav2 see it as obstacle.
            if dst and dst.get("id"):
                self._set_location_state(dst["id"], "occupied")

            # ── Step 4b: Retreat — reverse straight to the drop lane foot
            # Same pattern as the pickup extract: one pure-linear move all
            # the way back to the aisle foot, no intermediate waypoint =
            # no rotation while still in the rack slot.
            if dst:
                dst_yaw = dst.get("yaw", 0.0)
                dst_foot = self._find_lane_approach_foot(
                    dst["x"], dst["y"], dst_yaw)
                self._update_robot_pose()
                rx = self._robot_pose["x"]
                ry = self._robot_pose["y"]
                yaw = self._robot_pose["yaw"]
                if dst_foot is not None:
                    foot_x, foot_y = dst_foot[0], dst_foot[1]
                    fwd_to_foot = ((foot_x - rx) * math.cos(yaw)
                                   + (foot_y - ry) * math.sin(yaw))
                    rev_dist = -fwd_to_foot
                    if rev_dist > 0.05:
                        self._log(f"  Reversing {rev_dist:.2f} m straight "
                                  f"back to drop aisle foot "
                                  f"({foot_x:.2f}, {foot_y:.2f}).")
                        self._crawl(-spd, 0.0, rev_dist,
                                    label="Retreating to aisle")
                    elif carry_d > 0:
                        self._crawl(-spd, 0.0, carry_d,
                                    label="Retreating (fallback)")
                elif carry_d > 0:
                    self._crawl(-spd, 0.0, carry_d,
                                label="Retreating to aisle")

            self._log(f"Transport complete: "
                      f"{task.get('from_name')} → {task.get('to_name')}")
            return True
        finally:
            # Always restore normal footprint + inflation, even on early returns
            if self._carrying:
                self._set_carrying_mode(False)
            else:
                self._set_inflation(self.p_inflation_normal)

    def _execute_navigate(self, task):
        """Simple point-to-point navigation."""
        dst = task.get("to", {})
        self._current_task_step = "navigating"
        self._state = MissionState.NAVIGATING
        self._log(f"Navigating to {task.get('to_name', 'target')}...")
        ok = self._navigate_to(dst["x"], dst["y"], dst.get("yaw", 0.0))
        return ok

    def _navigate_to(self, x, y, yaw):
        """Send a goal to Nav2 and block until completion."""
        # Wait for Nav2 with retries — the nav stack may still be initializing
        for attempt in range(3):
            if self._nav_client.wait_for_server(timeout_sec=self.p_nav_timeout):
                break
            self._log(f"  Waiting for Nav2 action server (attempt {attempt + 1}/3)...")
        else:
            self._log("ERROR: Nav2 action server not available!")
            return False

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = float(x)
        goal.pose.pose.position.y = float(y)
        goal.pose.pose.position.z = 0.0

        # yaw to quaternion
        goal.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
        goal.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

        self.get_logger().info(f"Sending Nav2 goal: ({x:.2f}, {y:.2f}, yaw={yaw:.2f})")
        future = self._nav_client.send_goal_async(
            goal, feedback_callback=self._nav_feedback_cb
        )

        # Wait for goal acceptance (callbacks fire from the main executor)
        t0 = time.time()
        while not future.done():
            if time.time() - t0 > self.p_nav_timeout:
                self._log("Nav2 goal send timed out!")
                return False
            time.sleep(self.p_nav_poll)

        goal_handle = future.result()
        if goal_handle is None or not goal_handle.accepted:
            self._log("Nav2 goal rejected!")
            return False

        self._nav_goal_handle = goal_handle
        result_future = goal_handle.get_result_async()

        # Block until nav completes
        while not result_future.done():
            if self._cancel_requested or self._paused:
                # Cancel the goal if we're aborting
                try:
                    goal_handle.cancel_goal_async()
                except Exception:
                    pass
                return False
            time.sleep(0.1)

        self._nav_goal_handle = None
        result = result_future.result()
        if result.status == 4:  # STATUS_SUCCEEDED
            self._log("Navigation goal reached.")
            return True
        else:
            self._log(f"Navigation failed with status {result.status}")
            return False

    def _nav_feedback_cb(self, feedback_msg):
        fb = feedback_msg.feedback
        # Estimate progress from distance remaining
        dist = fb.distance_remaining if hasattr(fb, "distance_remaining") else 0.0
        self._progress = max(0.0, 1.0 - dist / 10.0)  # rough estimate

    def _scan_cb(self, name, msg):
        """Store latest laser scan for rack-leg detection."""
        self._latest_scans[name] = msg

    # ─────────────────────────────────────────────────────────────────
    # Lane graph + waypoint following
    # ─────────────────────────────────────────────────────────────────
    # Lanes are user-drawn polylines stored in locations.json under
    # "lanes": [{ id, name, points: [{x, y}, ...] }, ...].  Polylines
    # whose endpoints are close are merged into a single graph node so
    # intersecting lanes form a connected graph.

    LANE_MERGE_DIST = 0.5  # nodes within this distance (m) are the same node

    def _build_lane_graph(self):
        """Return (points, edges).

        points: list of (x, y) coordinates indexed by node id.
        edges: dict {node_id: [(neighbor_id, distance), ...]}.
        """
        from collections import defaultdict
        thresh_sq = self.LANE_MERGE_DIST ** 2
        points = []
        edges = defaultdict(list)

        def find_or_add(x, y):
            for i, (px, py) in enumerate(points):
                if (px - x) ** 2 + (py - y) ** 2 < thresh_sq:
                    return i
            points.append((x, y))
            return len(points) - 1

        for lane in self._locations.get("lanes", []):
            prev = None
            for pt in lane.get("points", []):
                idx = find_or_add(float(pt["x"]), float(pt["y"]))
                if prev is not None and prev != idx:
                    px1, py1 = points[idx]
                    px0, py0 = points[prev]
                    d = math.hypot(px1 - px0, py1 - py0)
                    edges[idx].append((prev, d))
                    edges[prev].append((idx, d))
                prev = idx

        return points, edges

    def _nearest_lane_node(self, x, y, points):
        """Return the index of the lane node closest to (x, y), or None."""
        best, best_d = None, float("inf")
        for i, (px, py) in enumerate(points):
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    @staticmethod
    def _project_to_segment(px, py, ax, ay, bx, by):
        """Project (px,py) onto segment a→b. Returns (foot_x, foot_y, t)
        with t clamped to [0, 1]."""
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq < 1e-9:
            return ax, ay, 0.0
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        return ax + t * dx, ay + t * dy, t

    def _find_lane_approach_foot(self, target_x, target_y, target_yaw):
        """Find the best perpendicular foot on the lane network for a rack
        or drop position.

        Logic:
          - Project the target onto every lane segment.
          - Strongly prefer feet that lie on the *approach side* of the
            target (opposite to target_yaw — that's where the aisle is).
          - Among approach-side feet, pick the closest perpendicular dist.
          - Fall back to wrong-side feet only if no approach-side one exists.

        Returns (foot_x, foot_y, a_node_idx, b_node_idx, segment_t) where
        a_node_idx/b_node_idx are the two graph nodes that the lane segment
        connects, so the foot can be inserted into the routing graph.
        Returns None if no lane exists / no segment has a usable foot.
        """
        points, _ = self._build_lane_graph()
        if not points:
            return None

        thresh_sq = self.LANE_MERGE_DIST ** 2

        def find_node(x, y):
            for i, (px, py) in enumerate(points):
                if (px - x) ** 2 + (py - y) ** 2 < thresh_sq:
                    return i
            return None

        # Approach direction (where the aisle/lane should be relative to the rack)
        approach_dx = -math.cos(target_yaw)
        approach_dy = -math.sin(target_yaw)

        best = None
        best_score = float("inf")

        for lane in self._locations.get("lanes", []):
            pts = lane.get("points", [])
            for si in range(len(pts) - 1):
                ax = float(pts[si]["x"])
                ay = float(pts[si]["y"])
                bx = float(pts[si + 1]["x"])
                by = float(pts[si + 1]["y"])

                fx, fy, t = self._project_to_segment(target_x, target_y,
                                                    ax, ay, bx, by)
                vx, vy = fx - target_x, fy - target_y
                dist = math.hypot(vx, vy)
                # Dot product: positive ⇒ foot is on approach side
                dot = vx * approach_dx + vy * approach_dy

                # Score: approach-side dramatically preferred
                score = dist if dot >= 0 else dist + 1000.0

                if score < best_score:
                    a_node = find_node(ax, ay)
                    b_node = find_node(bx, by)
                    if a_node is None or b_node is None:
                        continue
                    best_score = score
                    best = (fx, fy, a_node, b_node, t)

        return best

    def _find_lane_path(self, start_xy, end_xy):
        """Dijkstra over the lane graph.

        Returns list of (x, y) waypoints starting at the lane node nearest
        to *start_xy* and ending at the lane node nearest to *end_xy*, or
        None if no lanes are defined or the graph is disconnected.
        """
        import heapq
        points, edges = self._build_lane_graph()
        if not points:
            return None
        start = self._nearest_lane_node(start_xy[0], start_xy[1], points)
        end   = self._nearest_lane_node(end_xy[0],   end_xy[1],   points)
        if start is None or end is None:
            return None
        if start == end:
            return [points[start]]

        dist = {start: 0.0}
        prev = {}
        pq = [(0.0, start)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == end:
                break
            if d > dist.get(u, float("inf")):
                continue
            for v, w in edges.get(u, []):
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        if end not in prev and start != end:
            return None
        path_idx = [end]
        while path_idx[-1] != start:
            path_idx.append(prev[path_idx[-1]])
        path_idx.reverse()
        return [points[i] for i in path_idx]

    def _route_via_lanes(self, target_x, target_y, target_yaw, label=""):
        """MANDATORY lane routing with perpendicular dock/undock.

        Algorithm:
          1. Find the perpendicular foot of the rack/drop position on the
             lane network, on the *approach side* (opposite to target_yaw).
             This foot is where the robot will leave the lane to approach
             the rack perpendicularly.
          2. Build the lane graph and pick whichever endpoint of the
             foot's segment is cheapest to reach from the robot's current
             position (Dijkstra).
          3. Walk the lane graph: current pose → nearest lane node →
             through the graph → foot's segment endpoint → foot point.
          4. The final waypoint (the foot) is approached with yaw =
             target_yaw, so the robot is already aligned for the
             perpendicular hop from foot → staging → rack.

        Returns True on success / no lanes defined / disconnected graph,
        False if a Nav2 hop failed.
        """
        lanes = self._locations.get("lanes", [])
        if not lanes:
            return True

        points, edges = self._build_lane_graph()
        if not points:
            return True

        # Step 1: find perpendicular foot on the approach side
        foot_info = self._find_lane_approach_foot(target_x, target_y, target_yaw)
        if foot_info is None:
            self._log("  Lane routing: no perpendicular foot found.")
            return True
        foot_x, foot_y, a_node, b_node, t = foot_info

        # Step 2: Dijkstra from the robot to all reachable lane nodes
        self._update_robot_pose()
        start_xy = (self._robot_pose["x"], self._robot_pose["y"])
        start_node = self._nearest_lane_node(start_xy[0], start_xy[1], points)
        if start_node is None:
            return True

        import heapq
        dist = {start_node: 0.0}
        prev = {}
        pq = [(0.0, start_node)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist.get(u, float("inf")):
                continue
            for v, w in edges.get(u, []):
                nd = d + w
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(pq, (nd, v))

        # Cost to reach foot via either segment endpoint = graph dist +
        # remaining along the segment to the foot itself.
        def reach_cost(node):
            if node not in dist:
                return float("inf")
            nx, ny = points[node]
            return dist[node] + math.hypot(nx - foot_x, ny - foot_y)

        a_cost = reach_cost(a_node)
        b_cost = reach_cost(b_node)
        if a_cost == float("inf") and b_cost == float("inf"):
            self._log("  Lane routing: graph disconnected from start.")
            return True
        end_node = a_node if a_cost <= b_cost else b_node

        # Step 3: reconstruct node path from start to end
        if end_node == start_node:
            node_path = [start_node]
        else:
            node_path = [end_node]
            while node_path[-1] != start_node:
                if node_path[-1] not in prev:
                    self._log("  Lane routing: cannot reach foot endpoint.")
                    return True
                node_path.append(prev[node_path[-1]])
            node_path.reverse()

        # Build the actual waypoints: lane nodes + the perpendicular foot
        waypoints = [points[i] for i in node_path]
        # If the foot coincides with the last node (foot is at an endpoint
        # of the segment), skip the redundant final hop.
        last_x, last_y = waypoints[-1]
        if math.hypot(last_x - foot_x, last_y - foot_y) > 0.05:
            waypoints.append((foot_x, foot_y))

        self._log(f"  Lane routing{f' ({label})' if label else ''}: "
                  f"{len(waypoints)} waypoint(s), exiting lane at "
                  f"foot ({foot_x:.2f}, {foot_y:.2f}) — "
                  f"perpendicular to target ({target_x:.2f}, {target_y:.2f}).")

        # Step 4: walk waypoints
        for i, (lx, ly) in enumerate(waypoints):
            if i < len(waypoints) - 1:
                nx, ny = waypoints[i + 1]
                yaw = math.atan2(ny - ly, nx - lx)
            else:
                # FINAL waypoint (the foot) — align with target_yaw so
                # the next hop (foot → staging) is a pure forward move
                # in the rack's approach direction.
                yaw = target_yaw
            self._log(f"    Waypoint {i + 1}/{len(waypoints)}: "
                      f"({lx:.2f}, {ly:.2f}, yaw={yaw:.2f})")
            ok = self._navigate_to(lx, ly, yaw)
            if not ok:
                return False
            if self._cancel_requested or self._paused:
                return False
        return True

    def _follow_lane_path(self, waypoints, final_yaw):
        """Navigate sequentially through waypoints.

        Each goal's yaw points toward the next waypoint; the last goal
        uses *final_yaw* (so the robot ends up oriented correctly for
        whatever comes next, e.g. a drop-staging crawl).
        """
        if not waypoints or len(waypoints) < 2:
            return True
        for i in range(1, len(waypoints)):
            x, y = waypoints[i]
            if i < len(waypoints) - 1:
                nx, ny = waypoints[i + 1]
                yaw = math.atan2(ny - y, nx - x)
            else:
                yaw = final_yaw
            self._log(f"  Lane waypoint {i}/{len(waypoints) - 1}: "
                      f"({x:.2f}, {y:.2f}, yaw={yaw:.2f})")
            ok = self._navigate_to(x, y, yaw)
            if not ok:
                return False
            if self._cancel_requested or self._paused:
                return False
        return True

    # ─────────────────────────────────────────────────────────────────
    # Rack-leg detection & centering
    # ─────────────────────────────────────────────────────────────────

    def _detect_rack_legs(self):
        """Detect 4 rack legs from lidar scans.

        Returns list of (x, y) centroids in base_footprint frame,
        or None if detection fails.
        """
        all_points = []
        scan_count = len(self._latest_scans)

        for name, scan in self._latest_scans.items():
            if scan is None:
                continue

            # Get transform from scan frame → base_footprint
            try:
                tf_stamped = self._tf_buffer.lookup_transform(
                    'base_footprint', scan.header.frame_id,
                    rclpy.time.Time())
                tx = tf_stamped.transform.translation.x
                ty = tf_stamped.transform.translation.y
                qz = tf_stamped.transform.rotation.z
                qw = tf_stamped.transform.rotation.w
                tf_yaw = 2.0 * math.atan2(qz, qw)
            except Exception:
                continue

            cos_t = math.cos(tf_yaw)
            sin_t = math.sin(tf_yaw)

            angle = scan.angle_min
            for r in scan.ranges:
                if self.p_leg_range_min < r < self.p_leg_range_max:
                    # Point in scan frame
                    sx = r * math.cos(angle)
                    sy = r * math.sin(angle)
                    # Transform to base_footprint
                    bx = cos_t * sx - sin_t * sy + tx
                    by = sin_t * sx + cos_t * sy + ty
                    # Only keep points within the expected rack-leg zone
                    bdist = math.sqrt(bx * bx + by * by)
                    if self.p_leg_dist_min < bdist < self.p_leg_dist_max:
                        all_points.append((bx, by))
                angle += scan.angle_increment

        if len(all_points) < 4:
            self.get_logger().debug(
                f"[LegDetect] Too few points: {len(all_points)} "
                f"(scans={scan_count})")
            return None

        # ── Cluster nearby points (legs are ~5cm wide, well separated)
        clusters = self._cluster_scan_points(all_points, max_dist=self.p_leg_cluster_d)

        if len(clusters) < 4:
            self.get_logger().debug(
                f"[LegDetect] Too few clusters: {len(clusters)} "
                f"from {len(all_points)} points")
            return None

        import itertools

        # Take up to the 10 largest clusters to search for a valid rack pattern
        # This prevents failure when adjacent racks are too close and their legs
        # form larger clusters than the target rack.
        clusters.sort(key=lambda c: c[2], reverse=True)
        top_clusters = clusters[:10]
        
        valid_legs = None
        
        for combo in itertools.combinations(top_clusters, 4):
            legs = [(c[0], c[1]) for c in combo]
            
            # ── Validate: 4 legs should form a roughly square pattern
            #    For a square with side 0.95m:
            #      4 side-distances ≈ 0.95m
            #      2 diagonal-distances ≈ 1.34m
            dists = []
            for i in range(4):
                for j in range(i + 1, 4):
                    dx = legs[i][0] - legs[j][0]
                    dy = legs[i][1] - legs[j][1]
                    dists.append(math.sqrt(dx * dx + dy * dy))
            dists.sort()
            
            sides = dists[:4]
            diags = dists[4:]
            side_ok = all(self.p_rack_side_min < d < self.p_rack_side_max for d in sides)
            diag_ok = all(self.p_rack_diag_min < d < self.p_rack_diag_max for d in diags)
            
            if side_ok and diag_ok:
                valid_legs = legs
                break
                
        if not valid_legs:
            self.get_logger().debug(
                f"[LegDetect] Pattern validation failed for all combinations among {len(top_clusters)} clusters.")
            return None

        return valid_legs

    def _cluster_scan_points(self, points, max_dist=0.12):
        """Simple nearest-center clustering.

        Returns list of (center_x, center_y, count).
        """
        clusters = []  # [(sum_x, sum_y, count)]
        max_d2 = max_dist * max_dist

        for px, py in points:
            best_idx = None
            best_d2 = max_d2
            for i, (sx, sy, cnt) in enumerate(clusters):
                cx = sx / cnt
                cy = sy / cnt
                d2 = (px - cx) ** 2 + (py - cy) ** 2
                if d2 < best_d2:
                    best_idx = i
                    best_d2 = d2

            if best_idx is not None:
                s = clusters[best_idx]
                clusters[best_idx] = (s[0] + px, s[1] + py, s[2] + 1)
            else:
                clusters.append((px, py, 1))

        # Convert sums to centroids
        return [(sx / cnt, sy / cnt, cnt) for sx, sy, cnt in clusters]

    def _center_under_rack(self, fallback_x, fallback_y):
        """Servo the robot to the center of the detected rack legs.

        Uses lidar-based leg detection for closed-loop centering.
        Falls back to stored coordinates if detection fails.

        Returns True when the robot has converged to within
        ``centering_tolerance`` of either the detected leg centroid or
        the stored fallback coordinates.  Returns False on timeout so
        the caller can abort and avoid an unsafe lift.
        """
        tolerance = self.p_center_tol
        speed = self.p_center_speed
        MAX_ITERATIONS = self.p_center_iters
        use_lidar = True
        detection_failures = 0

        self._log("  Centering: scanning for rack legs...")
        self._log(f"  Scans available: {list(self._latest_scans.keys())}")

        for i in range(MAX_ITERATIONS):
            if self._cancel_requested or self._paused:
                self._cmd_vel_pub.publish(Twist())
                return False

            if use_lidar:
                legs = self._detect_rack_legs()
                if legs is not None:
                    detection_failures = 0
                    # Centroid of 4 legs in robot frame = offset to rack center
                    offset_x = sum(l[0] for l in legs) / 4
                    offset_y = sum(l[1] for l in legs) / 4
                    dist = math.sqrt(offset_x ** 2 + offset_y ** 2)

                    if i == 0 or i % 20 == 0:
                        self._log(
                            f"  Legs detected: offset=({offset_x:.3f}, {offset_y:.3f}), "
                            f"dist={dist:.3f}m")

                    if dist < tolerance:
                        self._cmd_vel_pub.publish(Twist())
                        self._log(f"  Centered! Error: {dist:.3f}m (lidar)")
                        return True

                    # Move toward centroid (offset is already in robot frame)
                    scale = min(speed / dist, 1.0) if dist > 0 else 0
                    twist = Twist()
                    twist.linear.x = offset_x * scale
                    twist.linear.y = offset_y * scale
                    self._cmd_vel_pub.publish(twist)
                    time.sleep(0.1)
                    continue
                else:
                    detection_failures += 1
                    # Log periodically so we can see what's happening
                    if detection_failures in (1, 5, 10, 20, 30):
                        self._log(
                            f"  Leg detection fail #{detection_failures} "
                            f"(scans={list(self._latest_scans.keys())})")

                    # Rotate in place to sweep lidars for better rack-leg visibility
                    if (detection_failures > 0
                            and detection_failures % self.p_center_rotate_after == 0
                            and detection_failures <= self.p_center_fail_n):
                        # Alternate CW / CCW each burst
                        burst = detection_failures // self.p_center_rotate_after
                        direction = 1.0 if burst % 2 == 1 else -1.0
                        self._log(
                            f"  Rotating to scan for legs "
                            f"({'CCW' if direction > 0 else 'CW'})...")
                        twist = Twist()
                        twist.angular.z = direction * self.p_center_rotate_speed
                        for _ in range(3):          # ~0.3 s burst
                            self._cmd_vel_pub.publish(twist)
                            time.sleep(0.1)
                        self._cmd_vel_pub.publish(Twist())
                        time.sleep(0.2)             # let new scans arrive

                    if detection_failures > self.p_center_fail_n:
                        self._log("  Rack legs not detected, falling back to coordinates")
                        use_lidar = False

            # Fallback: use stored coordinates
            self._update_robot_pose()
            dx = fallback_x - self._robot_pose["x"]
            dy = fallback_y - self._robot_pose["y"]
            dist = math.sqrt(dx * dx + dy * dy)

            if dist < tolerance:
                self._cmd_vel_pub.publish(Twist())
                self._log(f"  Centered! Error: {dist:.3f}m (coordinates)")
                return True

            yaw = self._robot_pose["yaw"]
            cos_y = math.cos(yaw)
            sin_y = math.sin(yaw)
            local_x = dx * cos_y + dy * sin_y
            local_y = -dx * sin_y + dy * cos_y
            scale = min(speed / dist, 1.0) if dist > 0 else 0
            twist = Twist()
            twist.linear.x = local_x * scale
            twist.linear.y = local_y * scale
            self._cmd_vel_pub.publish(twist)
            time.sleep(0.1)

        self._cmd_vel_pub.publish(Twist())
        self._log(f"  Centering timed out")
        return False

    def _crawl(self, vx, vy, distance, label=""):
        """Drive at (vx, vy) without Nav2 for *distance* metres.

        Pure straight-line movement — no angular velocity — so the carried
        rack doesn't swing into adjacent racks.
        """
        speed = math.sqrt(vx * vx + vy * vy)
        if speed <= 0 or distance <= 0:
            return
        duration = distance / speed
        if label:
            self._log(f"  {label} ({distance:.2f}m)...")
        twist = Twist()
        twist.linear.x = float(vx)
        twist.linear.y = float(vy)
        t0 = time.time()
        while time.time() - t0 < duration:
            if self._cancel_requested or self._paused:
                self._cmd_vel_pub.publish(Twist())
                return
            self._cmd_vel_pub.publish(twist)
            time.sleep(0.05)
        self._cmd_vel_pub.publish(Twist())

    def _lift_to(self, height):
        msg = Float64()
        msg.data = float(height)
        self._lift_pub.publish(msg)

    def _set_inflation(self, radius: float):
        """Update inflation_radius on both costmaps without touching footprint.

        Used to lower inflation for the *entire* transport task (not just
        carrying), so the planner can find paths into tight rack rows.
        """
        param = RclParameter(
            name='inflation_layer.inflation_radius',
            value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE,
                double_value=float(radius)))
        for client, name in [
            (self._local_costmap_param_cli, "local_costmap"),
            (self._global_costmap_param_cli, "global_costmap"),
        ]:
            self._apply_params(client, name, [param])
        self._log(f"Inflation radius → {radius:.2f}m")
        time.sleep(0.3)
        self._clear_costmaps()

    def _set_carrying_mode(self, carrying: bool):
        """Switch costmap footprint and obstacle layers for rack carrying."""
        self._carrying = carrying
        mode_str = "CARRYING" if carrying else "EMPTY"
        self._log(f"Footprint → {mode_str}")

        if carrying:
            footprint = self.p_footprint_carrying
            inflation = self.p_inflation_carrying
        else:
            footprint = self.p_footprint_robot
            inflation = self.p_inflation_normal

        # Build parameter list: footprint + reduced inflation when carrying.
        # Reduced inflation prevents static-map rack legs from merging into one
        # giant blob that traps the planner.  The larger carrying footprint
        # already provides the necessary safety margin.
        inflation_param = RclParameter(
            name='inflation_layer.inflation_radius',
            value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE,
                double_value=inflation))

        local_params = [
            RclParameter(
                name='footprint',
                value=ParameterValue(
                    type=ParameterType.PARAMETER_STRING,
                    string_value=footprint)),
            inflation_param,
        ]

        global_params = [
            RclParameter(
                name='footprint',
                value=ParameterValue(
                    type=ParameterType.PARAMETER_STRING,
                    string_value=footprint)),
            inflation_param,
        ]

        # Apply to each costmap
        for client, name, params in [
            (self._local_costmap_param_cli, "local_costmap", local_params),
            (self._global_costmap_param_cli, "global_costmap", global_params),
        ]:
            self._apply_params(client, name, params)

        # Clear costmaps so stale obstacles are removed
        time.sleep(0.3)
        self._clear_costmaps()

    def _apply_params(self, client, name, params):
        """Send a SetParameters request and wait briefly for response."""
        try:
            if not client.service_is_ready():
                self.get_logger().warn(
                    f"[Carrying] {name} param service not ready, skipping")
                return
            req = SetParameters.Request(parameters=params)
            future = client.call_async(req)
            t0 = time.time()
            while not future.done() and time.time() - t0 < 3.0:
                time.sleep(0.05)
            if future.done():
                resp = future.result()
                if resp and resp.results:
                    failed = [r.reason for r in resp.results if not r.successful]
                    if failed:
                        self.get_logger().warn(
                            f"[Carrying] {name} partial failure: {failed}")
                    else:
                        self.get_logger().info(f"[Carrying] {name} updated OK")
        except Exception as e:
            self.get_logger().warn(f"[Carrying] {name} error: {e}")

    def _clear_costmaps(self):
        """Clear both costmaps via ros2 CLI (infrequent operation)."""
        import subprocess
        for svc in [
            '/local_costmap/clear_entirely_local_costmap',
            '/global_costmap/clear_entirely_global_costmap',
        ]:
            try:
                subprocess.run(
                    ['ros2', 'service', 'call', svc,
                     'nav2_msgs/srv/ClearEntireCostmap', '{}'],
                    capture_output=True, timeout=5,
                )
                self.get_logger().info(f"[Carrying] Cleared {svc}")
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────
    # Rack-state registry (dynamic obstacles)
    # ─────────────────────────────────────────────────────────────────
    def _set_location_state(self, location_id: str, state: str):
        """Update the 'state' field of a rack or drop_zone and persist.

        Triggers an immediate /rack_obstacles republish so Nav2 sees the
        new occupancy on the next costmap update, and the GUI re-renders
        the colored square within ~200 ms (5 Hz state publish).
        """
        if state not in ("free", "occupied"):
            return
        changed = False
        name = location_id
        for key in ("racks", "drop_zones"):
            for loc in self._locations.get(key, []):
                if loc.get("id") == location_id or loc.get("name") == location_id:
                    name = loc.get("name") or loc.get("id") or location_id
                    if loc.get("state") != state:
                        loc["state"] = state
                        changed = True
                    break
        if changed:
            self._save_locations()
            self._publish_rack_obstacles()
            icon = "🟩" if state == "free" else "🟥"
            self._log(f"{icon} '{name}' is now {state.upper()}.")

    def _publish_rack_obstacles(self):
        """Publish a PointCloud2 of leg points for every OCCUPIED location.

        Excludes the rack the robot is currently carrying (that one is no
        longer at its original spot).  Each rack contributes 4 corner
        points so Nav2's obstacle layer marks lethal cells at the legs.
        """
        half = self.p_rack_side / 2.0
        offsets = [(-half, -half), (-half, half), (half, -half), (half, half)]

        carried_id = None
        if self._carrying and self._current_task:
            carried_id = self._current_task.get("rack_id") \
                or (self._current_task.get("from") or {}).get("id")

        points = []
        for key in ("racks", "drop_zones"):
            for loc in self._locations.get(key, []):
                if loc.get("state", "free") != "occupied":
                    continue
                if carried_id and loc.get("id") == carried_id:
                    continue
                cx, cy = loc.get("x", 0.0), loc.get("y", 0.0)
                yaw = loc.get("yaw", 0.0)
                cy_, sy_ = math.cos(yaw), math.sin(yaw)
                for ox, oy in offsets:
                    # Rotate the leg pattern by the rack's yaw
                    px = cx + cy_ * ox - sy_ * oy
                    py = cy + sy_ * ox + cy_ * oy
                    points.append((float(px), float(py), 0.10))

        msg = PointCloud2()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.height = 1
        msg.width = len(points)
        msg.fields = [
            PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
            PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
            PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
        ]
        msg.is_bigendian = False
        msg.point_step = 12
        msg.row_step = msg.point_step * msg.width
        msg.is_dense = True
        msg.data = b"".join(struct.pack("<fff", *p) for p in points)
        self._rack_obs_pub.publish(msg)

    # ─────────────────────────────────────────────────────────────────
    # State broadcasting
    # ─────────────────────────────────────────────────────────────────
    def _publish_state(self):
        self._update_robot_pose()
        state_data = {
            "state": self._state.value,
            "robot_pose": self._robot_pose,
            "current_task": self._current_task,
            "current_step": self._current_task_step,
            "progress": round(self._progress, 2),
            "queue": self._task_queue[:],
            "locations": self._locations,
            "speed": self._speed_multiplier,
            "carrying": self._carrying,
        }
        msg = String()
        msg.data = json.dumps(state_data)
        self._state_pub.publish(msg)

    def _log(self, text):
        self.get_logger().info(text)
        msg = String()
        msg.data = json.dumps({
            "timestamp": time.time(),
            "message": text,
        })
        self._feedback_pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = MissionManagerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
