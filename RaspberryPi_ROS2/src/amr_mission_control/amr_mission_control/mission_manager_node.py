"""
AMR Mission Control — Mission Manager Node

Manages a task queue of rack-transport missions, drives Nav2 goals,
controls the lift mechanism, and relays state/feedback to the web GUI
via rosbridge topics.

Topics Published:
  /mission_control/state     (std_msgs/String)            JSON state blob (5 Hz)
  /mission_control/feedback  (std_msgs/String)            Human-readable log lines
  /cmd_vel                   (geometry_msgs/Twist)        Teleop + closed-loop docking moves
  /lift_joint/cmd_pos        (std_msgs/Float64)           Lift height [m]
  /rack_obstacles            (sensor_msgs/PointCloud2)    Legs of occupied racks (latched)
  /payload/leg_mask          (std_msgs/Float32MultiArray) Carried-rack legs for payload_filter (latched)

Topics Subscribed:
  /mission_control/command   (std_msgs/String)            JSON commands from GUI
  /odometry/filtered         (nav_msgs/Odometry)          Measured velocity (EKF)
  scan_topics parameter      (sensor_msgs/LaserScan)      Filtered scans for rack-leg detection

Actions Used:
  /navigate_to_pose          (nav2_msgs/NavigateToPose)
  /follow_path               (nav2_msgs/FollowPath)       Smoothed lane routes
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

from std_msgs.msg import String, Float64, Header, Float32MultiArray
from geometry_msgs.msg import Twist, PoseStamped
from nav_msgs.msg import Odometry, Path
from sensor_msgs.msg import LaserScan, PointCloud2, PointField
from nav2_msgs.action import NavigateToPose, FollowPath
from rcl_interfaces.srv import SetParameters
from rcl_interfaces.msg import Parameter as RclParameter
from rcl_interfaces.msg import ParameterValue, ParameterType
from std_srvs.srv import SetBool

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
        # Id of the rack we're actively docking under (approach/centering).
        # Excluded from /rack_obstacles so the planner doesn't treat the
        # rack we're driving *under* as a lethal wall.
        self._docking_target_id = None
        self._latest_scans = {}  # {topic: LaserScan}
        # Latest measured body velocity (from EKF) for dock diagnostics.
        self._odom_vel = (0.0, 0.0, 0.0)
        # Carried-rack leg positions in base_footprint (captured at dock),
        # broadcast to the payload_filter so the scanner ignores them while
        # carrying.  Empty ⇒ not carrying.
        self._payload_legs = []
        self._lock = Lock()
        self._paused = False
        self._cancel_requested = False
        self._nav_goal_handle = None

        # ── Locations ────────────────────────────────────────────────
        # Persist to ~/.ros/amr_locations.json by default — survives
        # colcon rebuilds (the install/.../config/locations.json copy
        # gets clobbered every build, wiping any GUI edits).
        param_path = self.declare_parameter(
            "locations_file", ""
        ).get_parameter_value().string_value

        persistent_path = os.path.expanduser("~/.ros/amr_locations.json")
        if param_path:
            self._locations_file = param_path
        else:
            self._locations_file = persistent_path
            # Pick up locations saved before the AGV -> AMR rename.
            legacy_path = os.path.expanduser("~/.ros/agv_locations.json")
            if not os.path.exists(persistent_path) and os.path.exists(legacy_path):
                import shutil
                shutil.copy(legacy_path, persistent_path)
                self.get_logger().info(
                    f"Migrated locations from {legacy_path}")
            # Seed the persistent file from the package share copy the
            # FIRST TIME the system runs, so we don't start blank.
            if not os.path.exists(persistent_path):
                from ament_index_python.packages import get_package_share_directory
                try:
                    pkg = get_package_share_directory("amr_mission_control")
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

        # Carried-leg mask for the payload_filter (latched so the filter
        # picks up the current state even if it (re)starts late).
        self._payload_mask_pub = self.create_publisher(
            Float32MultiArray, "/payload/leg_mask", rack_obs_qos)

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

        # ── Odometry (measured velocity, for dock diagnostics) ───────
        self.create_subscription(
            Odometry, "/odometry/filtered", self._odom_cb, scan_qos,
            callback_group=self.cb_group,
        )

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
        # Velocity smoother — caps the autonomous travel speed.  The GUI
        # speed slider drives this so it actually affects mission speed
        # (not just teleop).
        self._smoother_param_cli = self.create_client(
            SetParameters,
            '/velocity_smoother/set_parameters',
            callback_group=self.cb_group,
        )
        # Hardware e-stop (CAN bridge -> STM32F411 e-stop controller).
        # Not available in simulation.
        self._estop_cli = self.create_client(
            SetBool, '/estop', callback_group=self.cb_group)

        # ── Nav2 action clients ──────────────────────────────────────
        self._nav_client = ActionClient(
            self, NavigateToPose, "navigate_to_pose",
            callback_group=self.cb_group,
        )
        # Controller server directly: follows a smoothed lane path
        # without the planner re-routing it.
        self._follow_client = ActionClient(
            self, FollowPath, "follow_path",
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

        # Rack-leg clustering
        self.p_leg_cluster_d  = g("leg_cluster_max_dist", 0.12).double_value

        # Transport timing
        # lift_height matches the URDF lift_range so the position controller
        # commands full travel without overshoot windup.
        self.p_lift_height    = g("lift_height", 0.20).double_value
        self.p_lift_wait      = g("lift_wait_time", 6.0).double_value
        self.p_settle_time    = g("costmap_settle_time", 1.0).double_value
        # Let the rack fully lower and settle on the floor before the robot
        # reverses out — otherwise it can drag the rack back with it.
        self.p_drop_wait      = g("drop_wait_time", 5.0).double_value

        # Speed
        self.p_default_speed  = g("default_speed", 0.4).double_value
        self.p_teleop_lin     = g("teleop_linear_scale", 0.4).double_value
        self.p_teleop_ang     = g("teleop_angular_scale", 0.8).double_value

        # Navigation
        self.p_nav_timeout    = g("nav_goal_timeout", 10.0).double_value
        self.p_nav_poll       = g("nav_poll_rate", 0.05).double_value

        # Lanes: corners (including junctions between lanes) are rounded with
        # this radius and the curve is followed in one pass (FollowPath)
        # instead of stopping at every lane point.
        self.p_lane_smooth    = g("lane_smooth_follow", True).bool_value
        self.p_lane_radius    = g("lane_corner_radius", 0.6).double_value
        self.p_lane_step      = g("lane_path_step", 0.05).double_value
        self.p_lane_timeout   = g("lane_follow_timeout", 120.0).double_value

        # Staging distances (Nav2 goal in the aisle before the closed-loop moves)
        # Pickup: staging hop must sit fully OUTSIDE the rack footprint so
        #         the robot's body doesn't overlap the near legs (which the
        #         planner treats as lethal).  Needs to be at least
        #         rack_half (~0.475) + robot_half_len (0.35) + margin.
        #         The leg-servo dock controller closes the rest of the gap.
        self.p_pickup_approach  = g("pickup_approach_distance", 1.35).double_value
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
        # Low during the pickup approach: dock_servo (not Nav2) does the
        # precise under-rack drive, so Nav2 must NOT be repelled by the rack
        # legs its own lidar sees — otherwise it "fears" the staging point and
        # creeps/stalls at the rack mouth.
        self.p_inflation_pickup   = g("inflation_radius_pickup", 0.25).double_value
        self.p_inflation_carrying = g("inflation_radius_carrying", 0.25).double_value

        # ── Closed-loop docking controller ───────────────────────────
        # Robot half-extents (used to size staging so the footprint stops
        # AT the near legs instead of overlapping them).
        self.p_robot_half_len = g("robot_half_length", 0.35).double_value
        self.p_robot_half_wid = g("robot_half_width", 0.32).double_value

        # Line-following gains for the differential drive (see _follow_line):
        # gain_x: forward speed per metre still to go along the rack axis,
        # gain_y: heading correction [rad] per metre of lateral offset,
        # gain_yaw: turn rate [rad/s] per radian of heading error.
        self.p_dock_kx   = g("dock_gain_x", 0.9).double_value
        self.p_dock_ky   = g("dock_gain_y", 1.1).double_value
        self.p_dock_kth  = g("dock_gain_yaw", 1.4).double_value
        # Higher vx_max gives the robot momentum to carry through the
        # stiction catch as the wheels pass between the near legs.
        self.p_dock_vx_max = g("dock_vx_max", 0.28).double_value
        self.p_dock_wz_max = g("dock_wz_max", 0.6).double_value
        # Convergence tolerances (must hold for dock_converge_count cycles).
        self.p_dock_pos_tol = g("dock_pos_tolerance", 0.03).double_value
        self.p_dock_lat_tol = g("dock_lateral_tolerance", 0.04).double_value
        self.p_dock_yaw_tol = g("dock_yaw_tolerance", 0.05).double_value
        self.p_dock_converge_count = g("dock_converge_count", 4).integer_value
        # A diff drive cannot fix a lateral offset once it is at the rack
        # centre: back out this far and re-approach, at most max_retries times.
        self.p_dock_retry_backoff = g("dock_retry_backoff", 0.8).double_value
        self.p_dock_max_retries = g("dock_max_retries", 2).integer_value
        # Within this distance of the goal, stop steering onto the line and
        # only square up the heading.
        self.p_line_blend = g("line_blend_distance", 0.15).double_value
        # Forward motion is gated by alignment: it fades (but never stops —
        # see dock_vx_floor) as lateral/yaw error grows beyond tolerance, so
        # the robot keeps creeping in while it squares up instead of
        # stalling far back where only the near legs are visible.
        self.p_dock_gate_y   = g("dock_gate_y", 0.20).double_value
        self.p_dock_gate_yaw = g("dock_gate_yaw", 0.30).double_value
        # Minimum forward fraction even when misaligned (guarantees progress
        # into the rack, where detection geometry is best).
        self.p_dock_vx_floor = g("dock_vx_floor", 0.30).double_value
        # Servo loop rate, overall timeout, and how long detection may be
        # lost before falling back to the stored map coordinates.
        self.p_dock_rate = g("dock_rate_hz", 15.0).double_value
        self.p_dock_timeout = g("dock_timeout", 90.0).double_value
        self.p_dock_lost_timeout = g("dock_lost_timeout", 2.5).double_value
        # Prior-guided leg matching: a detected cluster matches a predicted
        # leg if within this gate; clusters farther than search_window from
        # the predicted rack centre are ignored (rejects adjacent racks).
        self.p_dock_match_gate = g("dock_leg_match_gate", 0.30).double_value
        self.p_dock_search_window = g("dock_search_window", 0.95).double_value

        # ── Closed-loop straight / point drives ──────────────────────
        # yaw_hold_gain / lateral_hold_gain play the roles of dock_gain_yaw /
        # dock_gain_y in _follow_line.
        self.p_drive_kp = g("drive_gain", 1.4).double_value
        self.p_drive_vmax = g("drive_vmax", 0.25).double_value
        self.p_drive_vmin = g("drive_vmin", 0.04).double_value
        self.p_drive_pos_tol = g("drive_pos_tolerance", 0.02).double_value
        self.p_drive_yaw_tol = g("drive_yaw_tolerance", 0.03).double_value
        self.p_drive_yaw_hold_k = g("drive_yaw_hold_gain", 1.2).double_value
        self.p_drive_lat_hold_k = g("drive_lateral_hold_gain", 1.2).double_value
        self.p_drive_rate = g("drive_rate_hz", 20.0).double_value
        self.p_drive_timeout = g("drive_timeout", 45.0).double_value

        # ── Stiction breakaway ───────────────────────────────────────
        # When commanding motion but the measured (EKF) body speed stays
        # near zero, the robot is caught (e.g. a momentary contact at the
        # rack entrance).  After dock_stall_time of this, we punch the
        # command up to dock_breakaway_v to break free, then resume normal
        # control.  Uses the act_v we already measure — no manual push.
        # Stalled = commanding motion but achieving less than stall_ratio of
        # it (catches a partial drag at the rack mouth, not just a dead stop).
        self.p_stall_speed_eps = g("stall_speed_eps", 0.03).double_value
        self.p_stall_ratio     = g("stall_ratio", 0.4).double_value
        self.p_stall_time      = g("stall_time", 0.35).double_value
        self.p_breakaway_v     = g("breakaway_velocity", 0.55).double_value

        # Extraction / retreat clearances.  Default = rack_half + robot_half
        # + margin so the carried rack ends fully in the aisle.
        self.p_extract_clearance = g("extract_clearance", 1.15).double_value
        self.p_retreat_clearance = g("retreat_clearance", 1.15).double_value

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
        elif action == "estop_release":
            self._handle_estop_release()
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
        self._call_hardware_estop(True)

    def _handle_estop_release(self):
        self._call_hardware_estop(False)
        if self._state == MissionState.ERROR:
            self._state = MissionState.IDLE
        self._log("Emergency stop release requested.")

    def _call_hardware_estop(self, active):
        if not self._estop_cli.service_is_ready():
            self._log("Hardware e-stop (/estop) not available — software stop only.")
            return
        future = self._estop_cli.call_async(SetBool.Request(data=active))
        future.add_done_callback(
            lambda f: self._log(f"Hardware e-stop: {f.result().message}"
                                if f.result() is not None
                                else "Hardware e-stop call failed."))

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
        az = float(cmd.get("az", 0.0))
        # Scale by speed multiplier (GUI sends ±1 relative values)
        # The speed values from the GUI are already in the 0.4 range,
        # so we scale by multiplier / 0.4.  Sideways (ly) is ignored: diff drive.
        scale = self._speed_multiplier / self.p_teleop_lin
        twist.linear.x = lx * scale
        twist.angular.z = az * scale
        self._cmd_vel_pub.publish(twist)

    def _handle_set_speed(self, cmd):
        speed = float(cmd.get("speed", 0.4))
        speed = max(0.1, min(1.0, speed))
        self._speed_multiplier = speed
        self._set_nav_max_speed(speed)
        self._log(f"Max speed set to {speed:.2f} m/s")

    def _set_nav_max_speed(self, speed):
        """Cap the autonomous travel speed via the velocity_smoother.

        The smoother sits at the end of the Nav2 velocity pipeline, so its
        max_velocity bounds how fast the robot drives during missions.  (The
        closed-loop docking/extract moves publish /cmd_vel directly and are
        intentionally governed by their own slow params.)
        """
        param = RclParameter(
            name='max_velocity',
            value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE_ARRAY,
                double_array_value=[float(speed), 0.0, 1.5]))
        self._apply_params(self._smoother_param_cli, "velocity_smoother", [param])

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
        """Transport: stage → leg-servo dock → lift → extract → nav-stage →
        insert → drop → retreat.

        All final-approach moves are CLOSED-LOOP (no dead-reckoning):
          - pickup centering  → _dock_servo (servo on the rack legs)
          - extraction/retreat → _drive_straight_closed_loop (odom)
          - drop insertion     → _drive_to_map_point (map localization)
        Reduced inflation is applied for the whole task so the planner can
        enter rack rows; restored on every exit path via try/finally.
        """
        src = task.get("from", {})
        dst = task.get("to", {})
        src_yaw = src.get("yaw", 0.0) if src else 0.0
        dst_yaw = dst.get("yaw", 0.0) if dst else 0.0
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
        self._set_inflation(self.p_inflation_pickup)
        time.sleep(self.p_settle_time)

        try:
            # ── Step 1: Navigate to PICKUP staging (aisle, outside legs) ─
            # Staging now sits fully outside the rack footprint (≈0.95 m
            # behind centre) so the robot body doesn't overlap the near
            # legs — otherwise the planner refuses to approach.  The
            # leg-servo dock closes the remaining gap.
            self._current_task_step = "navigating_to_rack"
            self._state = MissionState.NAVIGATING
            if src:
                # Stop treating the rack we're about to drive UNDER as a
                # lethal obstacle (its legs are still 'occupied').
                self._docking_target_id = src.get("id")
                self._publish_rack_obstacles()

                stg_x, stg_y = self._staging_point(
                    src["x"], src["y"], src_yaw, pickup_d)

                if not self._route_via_lanes(stg_x, stg_y, src_yaw,
                                             label="to pickup"):
                    return False
                if self._cancel_requested or self._paused:
                    return False

                self._log(f"Navigating to pickup staging for "
                          f"{task.get('from_name', 'rack')}...")
                if not self._navigate_to(stg_x, stg_y, src_yaw):
                    return False

            if self._cancel_requested or self._paused:
                return False

            # ── Step 1b: Closed-loop leg-servo dock (in + centre + align)
            if src:
                self._current_task_step = "centering"
                if not self._dock_servo(src["x"], src["y"], src_yaw,
                                        label=task.get("from_name", "rack")):
                    self._log("ERROR: Docking failed — aborting task to "
                              "avoid lifting without a rack underneath.")
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

            # Carrying now: legs are lifted out of the lidar plane.  The
            # carried rack is suppressed by _set_carrying_mode's logic.
            self._docking_target_id = None
            self._set_carrying_mode(True)
            time.sleep(self.p_settle_time)

            # ── Step 2b: Extract straight back into the aisle ────────
            # Pure straight reverse with lateral + yaw hold, closed-loop
            # on odom, so the carried rack pulls out without swinging.
            if src:
                self._drive_straight_closed_loop(
                    -self.p_extract_clearance,
                    label="Extracting rack to aisle")
                time.sleep(0.3)
                self._clear_costmaps()
                time.sleep(0.5)

            if self._cancel_requested or self._paused:
                return False

            # ── Step 3: Navigate to DROP staging point (in the aisle) ─
            self._current_task_step = "navigating_to_dest"
            self._state = MissionState.NAVIGATING
            if dst:
                stg_x, stg_y = self._staging_point(
                    dst["x"], dst["y"], dst_yaw, carry_d)

                if not self._route_via_lanes(stg_x, stg_y, dst_yaw,
                                             label="to drop"):
                    return False
                if self._cancel_requested or self._paused:
                    return False

                self._log(f"Navigating to drop staging for "
                          f"{task.get('to_name', 'destination')}...")
                if not self._navigate_to(stg_x, stg_y, dst_yaw):
                    return False

            if self._cancel_requested or self._paused:
                return False

            # ── Step 3b: Insert to the EXACT drop coordinate ─────────
            # No legs to servo on at an empty slot, so close the loop on
            # map localization: holonomic P-servo straight onto the drop
            # pose.  This is what fixes "leaves it in the aisle".
            if dst:
                self._current_task_step = "inserting"
                self._drive_to_map_point(
                    dst["x"], dst["y"], dst_yaw, label="drop insert")

            if self._cancel_requested or self._paused:
                return False

            # ── Step 4: Drop ─────────────────────────────────────────
            self._current_task_step = "dropping"
            self._state = MissionState.DROPPING
            self._log("Dropping rack...")
            self._lift_to(0.0)
            time.sleep(self.p_drop_wait)

            # Mark drop location as OCCUPIED — there's a rack there now.
            if dst and dst.get("id"):
                self._set_location_state(dst["id"], "occupied")

            # ── Step 4b: Retreat straight back into the aisle ────────
            if dst:
                self._drive_straight_closed_loop(
                    -self.p_retreat_clearance,
                    label="Retreating to aisle")

            self._log(f"Transport complete: "
                      f"{task.get('from_name')} → {task.get('to_name')}")
            return True
        finally:
            # Always restore normal footprint + inflation, even on early
            # returns, and stop suppressing the target rack.
            self._docking_target_id = None
            if self._carrying:
                self._set_carrying_mode(False)
            else:
                self._set_inflation(self.p_inflation_normal)
            self._publish_rack_obstacles()

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

    def _odom_cb(self, msg: Odometry):
        """Cache measured body velocity for docking diagnostics."""
        t = msg.twist.twist
        self._odom_vel = (t.linear.x, t.linear.y, t.angular.z)

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

        # Step 4: follow the route. The final pose (the foot) is aligned with
        # target_yaw so the next hop (foot → staging) is a pure forward move
        # in the rack's approach direction.
        return self._follow_lane_route(waypoints, target_yaw)

    def _follow_lane_route(self, waypoints, final_yaw):
        """Drive a lane route without stopping at its corners.

        Every interior point (lane bends and junctions between lanes) is
        rounded with lane_corner_radius and the curve goes to the controller
        in one FollowPath goal. If that action is unavailable or the controller
        gives up, the rest of the route falls back to one Nav2 goal per point
        (which still has the BT recoveries).
        """
        if not waypoints:
            return True
        if self.p_lane_smooth and len(waypoints) >= 2:
            result = self._follow_smoothed(waypoints)
            if result is True:
                xn, yn = waypoints[-1]
                return self._navigate_to(xn, yn, final_yaw)
            if result is False:
                return False
            self._update_robot_pose()
            rx, ry = self._robot_pose["x"], self._robot_pose["y"]
            nearest = min(range(len(waypoints)),
                          key=lambda i: math.hypot(waypoints[i][0] - rx, waypoints[i][1] - ry))
            waypoints = waypoints[nearest:]
            self._log("  Continuing the lane route point to point.")
        return self._follow_waypoints(waypoints, final_yaw)

    def _follow_waypoints(self, waypoints, final_yaw):
        """One Nav2 goal per waypoint; yaw points to the next, the last uses final_yaw."""
        for i, (lx, ly) in enumerate(waypoints):
            if i < len(waypoints) - 1:
                nx, ny = waypoints[i + 1]
                yaw = math.atan2(ny - ly, nx - lx)
            else:
                yaw = final_yaw
            self._log(f"    Waypoint {i + 1}/{len(waypoints)}: "
                      f"({lx:.2f}, {ly:.2f}, yaw={yaw:.2f})")
            ok = self._navigate_to(lx, ly, yaw)
            if not ok:
                return False
            if self._cancel_requested or self._paused:
                return False
        return True

    def _follow_smoothed(self, waypoints):
        """Follow the rounded lane route with the controller server.

        Returns True when the end is reached, False when the mission was
        cancelled/paused or the robot could not get onto the lane, and None
        when the smooth follow is unavailable or failed (caller falls back).
        """
        if not self._follow_client.wait_for_server(timeout_sec=2.0):
            self._log("  follow_path action not available.")
            return None

        # Get onto the lane at its first point, facing along it.
        self._update_robot_pose()
        x0, y0 = waypoints[0]
        if math.hypot(self._robot_pose["x"] - x0, self._robot_pose["y"] - y0) > 0.3:
            x1, y1 = waypoints[1]
            if not self._navigate_to(x0, y0, math.atan2(y1 - y0, x1 - x0)):
                return False
            if self._cancel_requested or self._paused:
                return False

        pts = self.smooth_polyline(waypoints, self.p_lane_radius, self.p_lane_step)
        path = Path()
        path.header.frame_id = "map"
        path.header.stamp = self.get_clock().now().to_msg()
        for i, (x, y) in enumerate(pts):
            a, b = (pts[i], pts[i + 1]) if i + 1 < len(pts) else (pts[i - 1], pts[i])
            yaw = math.atan2(b[1] - a[1], b[0] - a[0])
            ps = PoseStamped()
            ps.header = path.header
            ps.pose.position.x = float(x)
            ps.pose.position.y = float(y)
            ps.pose.orientation.z = math.sin(yaw / 2.0)
            ps.pose.orientation.w = math.cos(yaw / 2.0)
            path.poses.append(ps)

        goal = FollowPath.Goal()
        goal.path = path
        goal.controller_id = "FollowPath"
        if hasattr(goal, "goal_checker_id"):
            goal.goal_checker_id = "general_goal_checker"
        length = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                     for i in range(1, len(pts)))
        self._log(f"  Following lane curve: {length:.1f} m, corners rounded at "
                  f"{self.p_lane_radius:.2f} m.")

        future = self._follow_client.send_goal_async(goal)
        t0 = time.time()
        while not future.done():
            if time.time() - t0 > self.p_nav_timeout:
                self._log("  Lane curve goal send timed out.")
                return None
            time.sleep(self.p_nav_poll)
        handle = future.result()
        if handle is None or not handle.accepted:
            self._log("  Lane curve rejected by the controller.")
            return None

        self._nav_goal_handle = handle
        result_future = handle.get_result_async()
        t0 = time.time()
        while not result_future.done():
            if self._cancel_requested or self._paused:
                try:
                    handle.cancel_goal_async()
                except Exception:
                    pass
                return False
            if time.time() - t0 > self.p_lane_timeout:
                handle.cancel_goal_async()
                self._log("  Lane curve timed out.")
                return None
            time.sleep(0.1)
        self._nav_goal_handle = None
        status = result_future.result().status
        if status == 4:  # STATUS_SUCCEEDED
            return True
        self._log(f"  Lane curve ended with status {status}.")
        return None

    @staticmethod
    def smooth_polyline(points, radius, step):
        """Round every interior corner of a polyline with a circular arc and
        resample it every *step* metres.

        The tangent length at a corner is capped at half of each neighbouring
        segment, so arcs of consecutive corners never overlap. The GUI draws
        lanes with the same rule (gui/src/lib/lanes.js).
        """
        pts = []
        for x, y in points:
            if not pts or math.hypot(x - pts[-1][0], y - pts[-1][1]) > 1e-3:
                pts.append((float(x), float(y)))
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            (ax, ay), (bx, by), (cx, cy) = pts[i - 1], pts[i], pts[i + 1]
            l1, l2 = math.hypot(bx - ax, by - ay), math.hypot(cx - bx, cy - by)
            ux, uy = (bx - ax) / l1, (by - ay) / l1
            vx, vy = (cx - bx) / l2, (cy - by) / l2
            turn = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
            if abs(turn) < 1e-3 or radius <= 0:
                out.append((bx, by))
                continue
            half = math.tan(abs(turn) / 2.0)
            d = min(radius * half, 0.5 * l1, 0.5 * l2)
            r = d / half
            side = 1.0 if turn > 0 else -1.0
            sx, sy = bx - ux * d, by - uy * d
            ox, oy = sx - uy * r * side, sy + ux * r * side
            a0 = math.atan2(sy - oy, sx - ox)
            n = max(2, int(math.ceil(abs(turn) * r / max(step, 1e-3))))
            for k in range(n + 1):
                a = a0 + side * abs(turn) * k / n
                out.append((ox + r * math.cos(a), oy + r * math.sin(a)))
        out.append(pts[-1])

        res = [out[0]]
        carry = 0.0
        for (ax, ay), (bx, by) in zip(out, out[1:]):
            seg = math.hypot(bx - ax, by - ay)
            t = step - carry
            while t < seg:
                res.append((ax + (bx - ax) * t / seg, ay + (by - ay) * t / seg))
                t += step
            carry = seg - (t - step)
        if math.hypot(res[-1][0] - out[-1][0], res[-1][1] - out[-1][1]) > 1e-3:
            res.append(out[-1])
        return res

    # ─────────────────────────────────────────────────────────────────
    # Rack-leg clustering
    # ─────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────
    # Closed-loop docking controller
    # ─────────────────────────────────────────────────────────────────
    # Three primitives replace the old open-loop crawl + 4-leg centering:
    #   _dock_servo               — servo on detected rack legs (pickup).
    #   _drive_to_map_point       — holonomic P-servo to a map pose (drop).
    #   _drive_straight_closed_loop — odom straight move (extract/retreat).
    # All three are closed-loop (TF feedback), so they survive wheel slip
    # and odom drift on the real robot — no dead-reckoning.

    @staticmethod
    def _wrap_angle(a):
        """Wrap an angle to (-pi, pi]."""
        return math.atan2(math.sin(a), math.cos(a))

    @staticmethod
    def _clamp(v, lo, hi):
        return lo if v < lo else (hi if v > hi else v)

    def _get_tf_pose(self, target_frame, source_frame="base_footprint"):
        """Return (x, y, yaw) of *source_frame* in *target_frame*, or None."""
        try:
            t = self._tf_buffer.lookup_transform(
                target_frame, source_frame, rclpy.time.Time())
            p = t.transform.translation
            q = t.transform.rotation
            return (p.x, p.y, _quaternion_to_yaw(q.x, q.y, q.z, q.w))
        except Exception:
            return None

    def _breakaway(self, vx, vy, stall_since):
        """Anti-stiction: if we're commanding motion but the measured body
        speed is ~0, the robot is caught.  After p_stall_time, punch the
        command up to p_breakaway_v along the commanded direction to break
        free.  Returns (vx, vy, stall_since); stall_since is None when not
        stalled, else the timestamp the stall began.
        """
        cmd_mag = math.hypot(vx, vy)
        act_mag = math.hypot(self._odom_vel[0], self._odom_vel[1])
        # Stalled if we want to move but are dragging at < stall_ratio of the
        # command (or essentially stopped).  This catches the partial drag at
        # the rack mouth where act never quite reaches zero.
        stall_thresh = max(self.p_stall_speed_eps,
                           self.p_stall_ratio * cmd_mag)
        if cmd_mag > self.p_stall_speed_eps and act_mag < stall_thresh:
            now = time.time()
            if stall_since is None:
                return vx, vy, now
            if now - stall_since > self.p_stall_time:
                scale = self.p_breakaway_v / cmd_mag
                return vx * scale, vy * scale, stall_since
            return vx, vy, stall_since
        return vx, vy, None

    def _collect_leg_clusters(self, hint_x, hint_y):
        """Return lidar clusters (cx, cy, count) in base_footprint frame
        that fall within *search_window* of the predicted rack centre
        (hint).  Focusing on the hint rejects clutter from adjacent racks.
        """
        win = self.p_dock_search_window
        win2 = win * win
        all_points = []

        for name, scan in list(self._latest_scans.items()):
            if scan is None:
                continue
            tf = self._get_tf_pose("base_footprint", scan.header.frame_id)
            if tf is None:
                continue
            tx, ty, tf_yaw = tf
            cos_t, sin_t = math.cos(tf_yaw), math.sin(tf_yaw)

            angle = scan.angle_min
            for r in scan.ranges:
                if 0.05 < r < (win + 1.0):
                    sx = r * math.cos(angle)
                    sy = r * math.sin(angle)
                    bx = cos_t * sx - sin_t * sy + tx
                    by = sin_t * sx + cos_t * sy + ty
                    if (bx - hint_x) ** 2 + (by - hint_y) ** 2 < win2:
                        all_points.append((bx, by))
                angle += scan.angle_increment

        if len(all_points) < 2:
            return []
        clusters = self._cluster_scan_points(
            all_points, max_dist=self.p_leg_cluster_d)
        # Drop single-point clusters — too likely to be noise.
        return [c for c in clusters if c[2] >= 2]

    def _estimate_rack_in_base(self, map_x, map_y, map_yaw):
        """Estimate the rack pose in the robot (base_footprint) frame.

        Prior-guided: predict the 4 leg positions from the stored map pose
        + current localization, match each to the nearest observed lidar
        cluster, then solve the 2D rigid transform (Procrustes) that best
        aligns prediction→observation.  That transform IS the correction to
        the prior, giving a refined rack centre + yaw in the base frame.

        Works with 2–4 visible legs and tolerates localization error up to
        the match gate.  Returns (cx, cy, cyaw, n_matched) or None.
        """
        robot_map = self._get_tf_pose("map")
        if robot_map is None:
            return None
        rx, ry, ryaw = robot_map

        # Prior rack centre + axis yaw, expressed in the base frame.
        dx, dy = map_x - rx, map_y - ry
        c, s = math.cos(ryaw), math.sin(ryaw)
        pcx = dx * c + dy * s
        pcy = -dx * s + dy * c
        pyaw = self._wrap_angle(map_yaw - ryaw)

        # Predicted leg positions (rack square, side = rack_side).
        half = self.p_rack_side / 2.0
        legs_local = [(half, half), (half, -half), (-half, half), (-half, -half)]
        cc, ss = math.cos(pyaw), math.sin(pyaw)
        predicted = [
            (pcx + cc * lx - ss * ly, pcy + ss * lx + cc * ly)
            for lx, ly in legs_local
        ]

        clusters = self._collect_leg_clusters(pcx, pcy)
        if len(clusters) < 2:
            return None

        # Greedy nearest-cluster matching within the gate.
        gate2 = self.p_dock_match_gate ** 2
        matched_p, matched_o = [], []
        used = set()
        for px, py in predicted:
            best_i, best_d2 = None, gate2
            for i, (cx, cy, _) in enumerate(clusters):
                if i in used:
                    continue
                d2 = (cx - px) ** 2 + (cy - py) ** 2
                if d2 < best_d2:
                    best_i, best_d2 = i, d2
            if best_i is not None:
                used.add(best_i)
                matched_p.append((px, py))
                matched_o.append((clusters[best_i][0], clusters[best_i][1]))

        n = len(matched_p)
        if n < 2:
            return None

        # 2D Procrustes: find dθ, dt with O ≈ R(dθ)·P + dt.
        mpx = sum(p[0] for p in matched_p) / n
        mpy = sum(p[1] for p in matched_p) / n
        mox = sum(o[0] for o in matched_o) / n
        moy = sum(o[1] for o in matched_o) / n
        s_num = s_den = 0.0
        for (px, py), (ox, oy) in zip(matched_p, matched_o):
            pcx_, pcy_ = px - mpx, py - mpy
            ocx_, ocy_ = ox - mox, oy - moy
            s_num += pcx_ * ocy_ - pcy_ * ocx_
            s_den += pcx_ * ocx_ + pcy_ * ocy_
        dth = math.atan2(s_num, s_den)
        cdt, sdt = math.cos(dth), math.sin(dth)
        # Translation so the centroids line up after rotation.
        tx = mox - (cdt * mpx - sdt * mpy)
        ty = moy - (sdt * mpx + cdt * mpy)

        # Apply the correction to the prior rack centre + yaw.
        cx = cdt * pcx - sdt * pcy + tx
        cy = sdt * pcx + cdt * pcy + ty
        cyaw = self._wrap_angle(pyaw + dth)
        return (cx, cy, cyaw, n)

    def _follow_line(self, along, lateral, heading, k_along, k_lat, k_yaw,
                     vmax, vmin=0.0):
        """Unicycle line follower for the differential drive.

        Errors are the robot pose in the goal frame: *along* = distance still
        to go along the goal axis (negative = overshot), *lateral* = offset to
        the left of the axis, *heading* = robot yaw minus axis yaw.  Steers
        onto the axis while driving along it, then only squares up the
        heading within line_blend_distance of the goal.  Returns (v, wz).
        """
        v = self._clamp(k_along * along, -vmax, vmax)
        if 0 < abs(v) < vmin:
            v = math.copysign(vmin, v)
        direction = 1.0 if v >= 0 else -1.0
        blend = self._clamp(abs(along) / self.p_line_blend, 0.0, 1.0)
        heading_ref = -direction * math.atan(k_lat * lateral) * blend
        wz = self._clamp(k_yaw * self._wrap_angle(heading_ref - heading),
                         -self.p_dock_wz_max, self.p_dock_wz_max)
        return v, wz

    def _dock_servo(self, map_x, map_y, map_yaw, label="rack"):
        """Closed-loop docking under the rack along its axis (diff drive).

        Servos on the detected legs, so it converges on the *true* rack pose
        regardless of odom/localization drift: the robot follows the rack
        axis into the rack (_follow_line), forward speed gated on alignment.
        A lateral offset left at the centre cannot be removed sideways, so
        the robot backs out and re-approaches.  Falls back to a
        map-coordinate drive if the legs can't be seen.

        Returns True once converged within tolerance, False on failure.
        """
        dt = 1.0 / self.p_dock_rate
        converged = 0
        retries = 0
        lost = 0
        lost_limit = int(self.p_dock_lost_timeout * self.p_dock_rate)
        t0 = time.time()
        last_log = 0.0
        stall_since = None
        self._log(f"  Docking servo: locking onto {label} legs...")

        while True:
            if self._cancel_requested or self._paused:
                self._cmd_vel_pub.publish(Twist())
                return False
            if time.time() - t0 > self.p_dock_timeout:
                self._cmd_vel_pub.publish(Twist())
                self._log("  Docking timed out.")
                return False

            est = self._estimate_rack_in_base(map_x, map_y, map_yaw)
            if est is None:
                lost += 1
                self._cmd_vel_pub.publish(Twist())  # hold position
                if lost > lost_limit:
                    self._log("  Legs not visible — falling back to "
                              "map-coordinate docking.")
                    return self._drive_to_map_point(
                        map_x, map_y, map_yaw, label="dock (coord fallback)")
                time.sleep(dt)
                continue
            lost = 0

            # Robot pose in the rack frame: it must end on the rack centre,
            # heading along the rack axis.
            cx, cy, cyaw, n = est
            c, s = math.cos(cyaw), math.sin(cyaw)
            along = cx * c + cy * s
            lateral = cx * s - cy * c
            heading = -cyaw

            at_centre = (abs(along) < self.p_dock_pos_tol
                         and abs(heading) < self.p_dock_yaw_tol)
            if at_centre and abs(lateral) <= self.p_dock_lat_tol:
                converged += 1
                if converged >= self.p_dock_converge_count:
                    self._cmd_vel_pub.publish(Twist())
                    # Snapshot the measured leg positions (base frame) so the
                    # payload filter masks exactly where the legs really are.
                    half = self.p_rack_side / 2.0
                    self._payload_legs = [
                        (cx + c * lx - s * ly, cy + s * lx + c * ly)
                        for lx, ly in ((half, half), (half, -half),
                                       (-half, half), (-half, -half))
                    ]
                    self._log(f"  Docked. residual along={along:.3f} m, "
                              f"lateral={lateral:.3f} m, yaw={heading:.3f} rad, "
                              f"legs={n}")
                    return True
            elif at_centre:
                self._cmd_vel_pub.publish(Twist())
                if retries >= self.p_dock_max_retries:
                    self._log(f"  Docking failed: lateral offset {lateral:+.3f} m "
                              f"after {retries} retries.")
                    return False
                retries += 1
                self._log(f"  Lateral offset {lateral:+.3f} m at the centre — "
                          f"backing out to re-approach ({retries}/"
                          f"{self.p_dock_max_retries}).")
                if not self._drive_straight_closed_loop(
                        -self.p_dock_retry_backoff, label="Dock retry back-off"):
                    return False
                converged = 0
                continue
            else:
                converged = 0

            # Alignment gate: forward motion fades as lateral/yaw error
            # exceeds tolerance, but never drops below dock_vx_floor — the
            # diff drive needs forward motion to remove a lateral offset.
            ex_y = max(0.0, abs(lateral) - self.p_dock_lat_tol)
            ex_th = max(0.0, abs(heading) - self.p_dock_yaw_tol)
            gate = self._clamp(
                1.0 - ex_y / self.p_dock_gate_y - ex_th / self.p_dock_gate_yaw,
                self.p_dock_vx_floor, 1.0)

            v, wz = self._follow_line(
                along, lateral, heading, self.p_dock_kx, self.p_dock_ky,
                self.p_dock_kth, self.p_dock_vx_max)
            twist = Twist()
            twist.angular.z = wz
            # Punch through a stiction catch (e.g. rack entrance) if stalled.
            twist.linear.x, _, stall_since = self._breakaway(
                v * gate, 0.0, stall_since)
            self._cmd_vel_pub.publish(twist)

            # Throttled live telemetry (~1 Hz) for tuning.
            now = time.time()
            if now - last_log > 1.0:
                last_log = now
                ov = self._odom_vel
                self._log(f"  dock along={along:+.3f} lat={lateral:+.3f} m "
                          f"yaw={heading:+.3f} rad gate={gate:.2f} legs={n} "
                          f"cmd=({twist.linear.x:+.2f} m/s,{wz:+.2f} rad/s) "
                          f"act_v={ov[0]:+.2f}")
            time.sleep(dt)

    def _drive_to_map_point(self, tx, ty, tyaw, pos_tol=None, label=""):
        """Drive the robot centre onto a map pose along its axis (diff drive).

        Uses map TF each cycle, so it places the robot on an absolute
        coordinate (used for the drop, where there are no legs to servo
        on).  The robot follows the line through the target along *tyaw*,
        so it arrives straight, without swinging a carried rack.
        """
        pos_tol = self.p_drive_pos_tol if pos_tol is None else pos_tol
        dt = 1.0 / self.p_drive_rate
        t0 = time.time()
        if label:
            self._log(f"  Driving to ({tx:.2f}, {ty:.2f}) [{label}]...")

        while True:
            if self._cancel_requested or self._paused:
                self._cmd_vel_pub.publish(Twist())
                return False
            if time.time() - t0 > self.p_drive_timeout:
                self._cmd_vel_pub.publish(Twist())
                self._log("  Drive-to-point timed out.")
                return False

            pose = self._get_tf_pose("map")
            if pose is None:
                time.sleep(dt)
                continue
            rx, ry, ryaw = pose
            c, s = math.cos(tyaw), math.sin(tyaw)
            dx, dy = rx - tx, ry - ty
            along = -(dx * c + dy * s)
            lateral = -dx * s + dy * c
            heading = self._wrap_angle(ryaw - tyaw)

            if abs(along) < pos_tol and abs(heading) < self.p_drive_yaw_tol:
                self._cmd_vel_pub.publish(Twist())
                if abs(lateral) > pos_tol:
                    self._log(f"  Arrived with lateral offset {lateral:+.3f} m.")
                return True

            v, wz = self._follow_line(
                along, lateral, heading, self.p_drive_kp,
                self.p_drive_lat_hold_k, self.p_drive_yaw_hold_k,
                self.p_drive_vmax, self.p_drive_vmin)
            twist = Twist()
            twist.linear.x = v
            twist.angular.z = wz
            self._cmd_vel_pub.publish(twist)
            time.sleep(dt)

    def _drive_straight_closed_loop(self, distance, label=""):
        """Drive a signed *distance* straight along the robot's heading,
        closed-loop on odom, steering back onto the start line (diff drive).

        Positive = forward, negative = reverse.  Holding the line and the
        heading is what keeps a carried rack from swinging into neighbours
        during extraction.  Closed-loop on odom (continuous, no localization
        jumps), so wheel slip can't accumulate distance error.
        """
        start = self._get_tf_pose("odom")
        if start is None:
            self._log("  Straight drive: no odom TF — aborting move.")
            return False
        x0, y0, yaw0 = start
        c0, s0 = math.cos(yaw0), math.sin(yaw0)
        dt = 1.0 / self.p_drive_rate
        t0 = time.time()
        stall_since = None
        if label:
            self._log(f"  {label} ({distance:+.2f} m, closed-loop)...")

        while True:
            if self._cancel_requested or self._paused:
                self._cmd_vel_pub.publish(Twist())
                return False
            if time.time() - t0 > self.p_drive_timeout:
                self._cmd_vel_pub.publish(Twist())
                self._log("  Straight drive timed out.")
                return False

            pose = self._get_tf_pose("odom")
            if pose is None:
                time.sleep(dt)
                continue
            rx, ry, ryaw = pose
            ddx, ddy = rx - x0, ry - y0
            travelled = ddx * c0 + ddy * s0      # along the heading axis
            lateral = -ddx * s0 + ddy * c0       # drift off the line
            remaining = distance - travelled

            if abs(remaining) < self.p_drive_pos_tol:
                self._cmd_vel_pub.publish(Twist())
                return True

            v, wz = self._follow_line(
                remaining, lateral, self._wrap_angle(ryaw - yaw0),
                self.p_drive_kp, self.p_drive_lat_hold_k,
                self.p_drive_yaw_hold_k, self.p_drive_vmax, self.p_drive_vmin)
            twist = Twist()
            twist.angular.z = wz
            # Break free if the carried rack catches (e.g. leg scrape).
            twist.linear.x, _, stall_since = self._breakaway(v, 0.0, stall_since)
            self._cmd_vel_pub.publish(twist)
            time.sleep(dt)

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

    def _publish_payload_mask(self):
        """Broadcast carried-leg positions (base frame) to the payload filter.

        Non-empty only while carrying; empty array tells the filter to pass
        scans through unmodified.
        """
        msg = Float32MultiArray()
        if self._carrying and self._payload_legs:
            flat = []
            for x, y in self._payload_legs:
                flat.extend((float(x), float(y)))
            msg.data = flat
        else:
            msg.data = []
        self._payload_mask_pub.publish(msg)

    def _set_carrying_mode(self, carrying: bool):
        """Switch costmap footprint and obstacle layers for rack carrying."""
        self._carrying = carrying
        mode_str = "CARRYING" if carrying else "EMPTY"
        self._log(f"Footprint → {mode_str}")

        if not carrying:
            self._payload_legs = []
        # Tell the payload filter to start/stop masking the carried legs.
        self._publish_payload_mask()

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

        # Rack(s) to suppress as obstacles: the one being carried AND the
        # one we're actively docking under (so the planner/controller will
        # actually drive the footprint up to its legs instead of refusing).
        suppressed = {i for i in (carried_id, self._docking_target_id) if i}

        points = []
        for key in ("racks", "drop_zones"):
            for loc in self._locations.get(key, []):
                if loc.get("state", "free") != "occupied":
                    continue
                if loc.get("id") in suppressed:
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

        # Keep the payload mask fresh (cheap; survives a filter restart).
        self._publish_payload_mask()

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
            "lane_corner_radius": self.p_lane_radius if self.p_lane_smooth else 0.0,
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
