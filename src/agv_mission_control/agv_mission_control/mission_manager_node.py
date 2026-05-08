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

from std_msgs.msg import String, Float64
from geometry_msgs.msg import Twist, PoseStamped
from nav2_msgs.action import NavigateToPose
from rcl_interfaces.srv import SetParameters
from rcl_interfaces.msg import Parameter as RclParameter
from rcl_interfaces.msg import ParameterValue, ParameterType

from tf2_ros import Buffer, TransformListener

# ── Footprint constants ──────────────────────────────────────────────
# Robot only: 0.70m × 0.64m
FOOTPRINT_ROBOT = '[[0.35, 0.32], [0.35, -0.32], [-0.35, -0.32], [-0.35, 0.32]]'
# Robot + rack: rack legs at ±0.475m from center → footprint 1.05m × 1.05m
FOOTPRINT_CARRYING = '[[0.525, 0.525], [0.525, -0.525], [-0.525, -0.525], [-0.525, 0.525]]'
# Min obstacle range when carrying: rack legs are at ~0.67m from center
CARRYING_MIN_RANGE = 0.70


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

        # ── State ────────────────────────────────────────────────────
        self._state = MissionState.IDLE
        self._task_queue = []          # list of task dicts
        self._current_task = None      # currently executing task
        self._current_task_step = ""   # "navigating_to_rack" / "lifting" / etc.
        self._progress = 0.0           # 0.0 – 1.0
        self._robot_pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self._speed_multiplier = 0.4  # m/s, adjustable from GUI
        self._carrying = False  # True when rack is lifted
        self._lock = Lock()
        self._paused = False
        self._cancel_requested = False
        self._nav_goal_handle = None

        # ── Locations ────────────────────────────────────────────────
        self._locations_file = self.declare_parameter(
            "locations_file", ""
        ).get_parameter_value().string_value

        if not self._locations_file:
            # Fallback: look in share directory
            from ament_index_python.packages import get_package_share_directory
            pkg = get_package_share_directory("agv_mission_control")
            self._locations_file = os.path.join(pkg, "config", "locations.json")

        self._locations = self._load_locations()

        # ── Publishers ───────────────────────────────────────────────
        self._state_pub = self.create_publisher(String, "/mission_control/state", 10)
        self._feedback_pub = self.create_publisher(String, "/mission_control/feedback", 10)

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

        self._log("Mission Manager started. Waiting for commands.")
        self.get_logger().info("Mission Manager Node initialized.")

    # ─────────────────────────────────────────────────────────────────
    # Location persistence
    # ─────────────────────────────────────────────────────────────────
    def _load_locations(self):
        try:
            with open(self._locations_file, "r") as f:
                data = json.load(f)
            self.get_logger().info(f"Loaded locations from {self._locations_file}")
            return data
        except Exception as e:
            self.get_logger().warn(f"Could not load locations: {e}")
            return {"dock": {"x": 0, "y": 0, "yaw": 0, "name": "Dock"}, "racks": [], "drop_zones": []}

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
        else:
            self._log(f"Unknown command: {action}")

    # ─────────────────────────────────────────────────────────────────
    # Command handlers
    # ─────────────────────────────────────────────────────────────────
    def _handle_add_task(self, cmd):
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
        racks = cmd.get("racks", [])
        drop_zones = cmd.get("drop_zones", [])
        self._locations["racks"] = racks
        self._locations["drop_zones"] = drop_zones
        self._save_locations()

    def _handle_update_dock(self, cmd):
        self._locations["dock"] = {
            "x": cmd.get("x", 0.0),
            "y": cmd.get("y", 0.0),
            "yaw": cmd.get("yaw", 0.0),
            "name": cmd.get("name", "Dock"),
        }
        self._save_locations()
        self._log(f"Dock updated to ({cmd.get('x')}, {cmd.get('y')})")

    def _handle_teleop(self, cmd):
        twist = Twist()
        lx = float(cmd.get("lx", 0.0))
        ly = float(cmd.get("ly", 0.0))
        az = float(cmd.get("az", 0.0))
        # Scale by speed multiplier (GUI sends ±1 relative values)
        # The speed values from the GUI are already in the 0.4 range,
        # so we scale by multiplier / 0.4
        scale = self._speed_multiplier / 0.4
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

    def _execute_transport(self, task):
        """Transport: go to rack → lift → go to destination → drop."""
        src = task.get("from", {})
        dst = task.get("to", {})

        # Step 1: Navigate to rack
        self._current_task_step = "navigating_to_rack"
        self._state = MissionState.NAVIGATING
        self._log(f"Navigating to {task.get('from_name', 'rack')}...")
        if src:
            ok = self._navigate_to(src["x"], src["y"], src.get("yaw", 0.0))
            if not ok:
                return False

        if self._cancel_requested or self._paused:
            return False

        # Step 2: Lift
        self._current_task_step = "lifting"
        self._state = MissionState.LIFTING
        self._log("Lifting rack...")
        self._lift_to(1.0)
        time.sleep(2.0)  # Wait for lift to complete

        # Switch to carrying footprint and filter rack legs
        self._set_carrying_mode(True)

        if self._cancel_requested or self._paused:
            return False

        # Step 3: Navigate to destination
        self._current_task_step = "navigating_to_dest"
        self._state = MissionState.NAVIGATING
        self._log(f"Navigating to {task.get('to_name', 'destination')}...")
        if dst:
            ok = self._navigate_to(dst["x"], dst["y"], dst.get("yaw", 0.0))
            if not ok:
                return False

        if self._cancel_requested or self._paused:
            return False

        # Step 4: Drop
        self._current_task_step = "dropping"
        self._state = MissionState.DROPPING
        self._log("Dropping rack...")
        self._lift_to(0.0)
        time.sleep(2.0)

        # Revert to normal footprint
        self._set_carrying_mode(False)

        self._log(f"Transport complete: {task.get('from_name')} → {task.get('to_name')}")
        return True

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
        if not self._nav_client.wait_for_server(timeout_sec=5.0):
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
        timeout = 10.0
        t0 = time.time()
        while not future.done():
            if time.time() - t0 > timeout:
                self._log("Nav2 goal send timed out!")
                return False
            time.sleep(0.05)

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

    def _lift_to(self, height):
        msg = Float64()
        msg.data = float(height)
        self._lift_pub.publish(msg)

    def _set_carrying_mode(self, carrying: bool):
        """Switch costmap footprint and scan min_range for rack carrying."""
        self._carrying = carrying
        footprint = FOOTPRINT_CARRYING if carrying else FOOTPRINT_ROBOT
        min_range = CARRYING_MIN_RANGE if carrying else 0.0
        mode_str = "CARRYING" if carrying else "EMPTY"
        self._log(f"Footprint → {mode_str}")

        # Build parameter list
        params = [
            RclParameter(
                name='footprint',
                value=ParameterValue(
                    type=ParameterType.PARAMETER_STRING,
                    string_value=footprint,
                ),
            ),
        ]

        # Build scan min_range parameters (observation sources in voxel/obstacle layer)
        scan_source_params = []
        if carrying:
            # Increase min_range on all observation sources to ignore rack legs
            for src in ['scan_front', 'scan_rear', 'scan']:
                for layer in ['voxel_layer', 'obstacle_layer']:
                    scan_source_params.append(
                        RclParameter(
                            name=f'{layer}.{src}.obstacle_min_range',
                            value=ParameterValue(
                                type=ParameterType.PARAMETER_DOUBLE,
                                double_value=min_range,
                            ),
                        )
                    )
        else:
            for src in ['scan_front', 'scan_rear', 'scan']:
                for layer in ['voxel_layer', 'obstacle_layer']:
                    scan_source_params.append(
                        RclParameter(
                            name=f'{layer}.{src}.obstacle_min_range',
                            value=ParameterValue(
                                type=ParameterType.PARAMETER_DOUBLE,
                                double_value=0.0,
                            ),
                        )
                    )

        # Apply to both costmaps
        for client, name in [
            (self._local_costmap_param_cli, "local_costmap"),
            (self._global_costmap_param_cli, "global_costmap"),
        ]:
            try:
                if not client.service_is_ready():
                    self.get_logger().warn(
                        f"[Carrying] {name} param service not ready, skipping")
                    continue

                # Set footprint
                req = SetParameters.Request(parameters=params)
                future = client.call_async(req)
                # Wait briefly for response
                t0 = time.time()
                while not future.done() and time.time() - t0 < 2.0:
                    time.sleep(0.05)
                if future.done():
                    resp = future.result()
                    if resp and resp.results:
                        success = all(r.successful for r in resp.results)
                        if success:
                            self.get_logger().info(
                                f"[Carrying] {name} footprint updated")
                        else:
                            self.get_logger().warn(
                                f"[Carrying] {name} footprint update failed: "
                                f"{[r.reason for r in resp.results if not r.successful]}")

                # Set scan min_range (best-effort, some params may not exist)
                req2 = SetParameters.Request(parameters=scan_source_params)
                future2 = client.call_async(req2)
                t0 = time.time()
                while not future2.done() and time.time() - t0 < 2.0:
                    time.sleep(0.05)
                # Don't check result — some params may not exist on some costmaps

            except Exception as e:
                self.get_logger().warn(
                    f"[Carrying] Failed to update {name}: {e}")

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
