"""ROS 2 <-> CAN bridge for the AMR (SocketCAN via python-can).

Owns the robot's CAN bus (one process for every CAN device):
  * hoverboard mainboard (Hoverboard_Firmware VARIANT_CAN, node id board_id): the two
    hub motors of the differential drive on the centre axle;
  * STM32F411 body controller: e-stop authority, it switches the relay that powers the
    hoverboard board. Its lift / lights / buzzer frames belong in this node as well.

Subscribes:  cmd_vel            geometry_msgs/Twist (linear.x, angular.z; linear.y is ignored)
Publishes:   odom               nav_msgs/Odometry (wheel odometry, no TF)
             joint_states       sensor_msgs/JointState (left/right wheel)
             hoverboard/status  amr_interfaces/HoverboardStatus
             estop/status       amr_interfaces/SafetyStatus
Services:    estop              std_srvs/SetBool (true = latch e-stop, false = request release)
"""

import math
import struct
import threading
import time

import can
import rclpy
from amr_interfaces.msg import HoverboardStatus, SafetyStatus
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_srvs.srv import SetBool

from amr_hardware import hoverboard as hb

JOINTS = ("left_wheel_joint", "right_wheel_joint")
CAUSES = {
    hb.CAUSE_NONE: "none",
    hb.CAUSE_HOST_REQUEST: "host request",
    hb.CAUSE_BUTTON: "e-stop button",
    hb.CAUSE_HOST_TIMEOUT: "host timeout",
}


def _diagonal(values):
    cov = [0.0] * 36
    for i, v in enumerate(values):
        cov[i * 7] = v
    return cov


# x, y, z, roll, pitch, yaw. The EKF fuses only vx, vy (always 0) and vyaw from this message.
POSE_COVARIANCE = _diagonal([0.01, 0.01, 1e3, 1e3, 1e3, 0.05])
TWIST_COVARIANCE = _diagonal([0.0025, 0.0025, 1e3, 1e3, 1e3, 0.01])


class Board:
    """Latest data received from the hoverboard mainboard."""

    def __init__(self, node_id):
        self.node_id = node_id
        self.ticks = None
        self.rpm = (0, 0)
        self.current = (0.0, 0.0)
        self.status = None
        self.status_time = None


class CanBridge(Node):
    def __init__(self):
        super().__init__("can_bridge")

        def param(name, default):
            return self.declare_parameter(name, default).value

        interface = param("can_interface", "can0")
        self.board = Board(param("board_id", 1))
        self.kin = hb.DiffDriveKinematics(
            param("wheel_radius", 0.0825),
            param("wheel_separation", 0.55),
        )
        self.ticks_per_rev = param("ticks_per_revolution", 90.0)
        self.max_rpm = param("max_wheel_rpm", 150.0)
        self.cmd_timeout = param("cmd_vel_timeout", 0.5)
        self.status_timeout = param("status_timeout", 0.5)
        self.odom_frame = param("odom_frame", "odom")
        self.base_frame = param("base_frame", "base_footprint")
        rate = param("rate", 50.0)
        status_rate = param("status_rate", 10.0)

        self._lock = threading.Lock()
        self._cmd = (0.0, 0.0)
        self._cmd_time = -math.inf
        self._estop_requested = False
        self._safety = None
        self._safety_time = None
        self._last_ticks = None
        self._last_ticks_time = None
        self._wheel_angle = [0.0, 0.0]
        self._x = self._y = self._yaw = 0.0
        self._board_online = False
        self._estop_logged = None

        node_id = self.board.node_id
        self._frames = {
            hb.odom_id(node_id): "odom",
            hb.motion_id(node_id): "motion",
            hb.status_id(node_id): "status",
            hb.SAFETY_STATUS_ID: "safety",
        }
        self.bus = can.Bus(
            interface="socketcan",
            channel=interface,
            can_filters=[{"can_id": i, "can_mask": 0x7FF, "extended": False} for i in self._frames],
        )

        self.odom_pub = self.create_publisher(Odometry, "odom", 10)
        self.joint_pub = self.create_publisher(JointState, "joint_states", 10)
        self.board_pub = self.create_publisher(HoverboardStatus, "hoverboard/status", 10)
        self.safety_pub = self.create_publisher(SafetyStatus, "estop/status", 10)
        self.create_subscription(Twist, "cmd_vel", self._on_cmd_vel, 10)
        self.create_service(SetBool, "estop", self._on_estop)
        self.create_timer(1.0 / rate, self._step)
        self.create_timer(1.0 / status_rate, self._publish_status)
        self._notifier = can.Notifier(self.bus, [self._on_frame])

        self.get_logger().info(
            f"CAN bridge on {interface}: hoverboard id {node_id}, "
            f"wheel limit {self.max_rpm:.0f} rpm")

    # ── CAN receive (python-can notifier thread) ─────────────────────
    def _on_frame(self, msg):
        kind = self._frames.get(msg.arbitration_id)
        if kind is None or msg.is_extended_id or msg.is_remote_frame or msg.is_error_frame:
            return
        try:
            if kind == "odom":
                value = hb.decode_odom(msg.data)
            elif kind == "motion":
                value = hb.decode_motion(msg.data)
            elif kind == "status":
                value = hb.decode_status(msg.data)
            else:
                value = hb.decode_safety_status(msg.data)
        except struct.error:
            return
        now = time.monotonic()
        with self._lock:
            if kind == "odom":
                self.board.ticks = value
            elif kind == "motion":
                self.board.rpm = value[:2]
                self.board.current = value[2:]
            elif kind == "status":
                self.board.status = value
                self.board.status_time = now
            else:
                self._safety = value
                self._safety_time = now

    def _send(self, arbitration_id, data):
        try:
            self.bus.send(
                can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=False),
                timeout=0.01)
            return True
        except can.CanError as e:
            self.get_logger().warning(f"CAN send failed: {e}", throttle_duration_sec=2.0)
            return False

    def _safety_online(self, now):
        return self._safety_time is not None and now - self._safety_time < self.status_timeout

    # ── ROS callbacks ────────────────────────────────────────────────
    def _on_cmd_vel(self, msg):
        with self._lock:
            self._cmd = (msg.linear.x, msg.angular.z)
            self._cmd_time = time.monotonic()

    def _on_estop(self, request, response):
        with self._lock:
            self._estop_requested = request.data
        response.success = self._send(hb.ESTOP_ID, hb.encode_estop(request.data))
        if not response.success:
            response.message = "CAN send failed"
        elif request.data:
            response.message = "E-stop latched: hoverboard braking, controller cutting its power"
            self.get_logger().warning(response.message)
        else:
            response.message = "E-stop release requested (refused while the button is pressed)"
            self.get_logger().info(response.message)
        return response

    def _step(self):
        now = time.monotonic()
        with self._lock:
            safety_stop = (self._safety_online(now) and self._safety.estop_active)
            fresh = now - self._cmd_time < self.cmd_timeout
            # While e-stopped the wheels get 0 rpm, so release never resumes a stale command.
            v, wz = self._cmd if fresh and not (self._estop_requested or safety_stop) else (0.0, 0.0)
            ticks = self.board.ticks
            rpm = self.board.rpm

        self._send_wheel_command(v, wz)
        self._publish_odometry(ticks, rpm, now)

    def _send_wheel_command(self, v, wz):
        rpm = [hb.rad_s_to_rpm(w) for w in self.kin.inverse(v, wz)]
        peak = max(abs(r) for r in rpm)
        if peak > self.max_rpm:
            # Scale both wheels together so the curvature is kept.
            rpm = [r * self.max_rpm / peak for r in rpm]
        self._send(hb.cmd_id(self.board.node_id), hb.encode_cmd(rpm[0], rpm[1]))

    def _publish_odometry(self, ticks, rpm, now):
        if ticks is None:
            return
        if self._last_ticks is None:
            self._last_ticks, self._last_ticks_time = ticks, now
            return

        dt = now - self._last_ticks_time
        deltas = [hb.tick_delta(new, old) for new, old in zip(ticks, self._last_ticks)]
        self._last_ticks, self._last_ticks_time = ticks, now
        for delta, wheel_rpm in zip(deltas, rpm):
            # Counters restart at 0 when the board is powered again (e-stop relay,
            # brown-out): drop steps that disagree with the measured wheel speed.
            expected = wheel_rpm * self.ticks_per_rev / 60.0 * dt
            if abs(delta - expected) > 0.5 * self.ticks_per_rev + abs(expected):
                self.get_logger().warning(
                    "Hall tick jump (board restarted?), re-baselining odometry",
                    throttle_duration_sec=2.0)
                return

        angles = [d * 2.0 * math.pi / self.ticks_per_rev for d in deltas]
        distance, dyaw = self.kin.forward(*angles)
        heading = self._yaw + dyaw / 2.0
        self._x += distance * math.cos(heading)
        self._y += distance * math.sin(heading)
        self._yaw = math.atan2(math.sin(self._yaw + dyaw), math.cos(self._yaw + dyaw))
        for i, a in enumerate(angles):
            self._wheel_angle[i] += a

        wheel_speeds = [hb.rpm_to_rad_s(r) for r in rpm]
        v, wz = self.kin.forward(*wheel_speeds)
        stamp = self.get_clock().now().to_msg()

        odom = Odometry()
        odom.header.stamp = stamp
        odom.header.frame_id = self.odom_frame
        odom.child_frame_id = self.base_frame
        odom.pose.pose.position.x = self._x
        odom.pose.pose.position.y = self._y
        odom.pose.pose.orientation.z = math.sin(self._yaw / 2.0)
        odom.pose.pose.orientation.w = math.cos(self._yaw / 2.0)
        odom.pose.covariance = POSE_COVARIANCE
        odom.twist.twist.linear.x = v
        odom.twist.twist.angular.z = wz
        odom.twist.covariance = TWIST_COVARIANCE
        self.odom_pub.publish(odom)

        joints = JointState()
        joints.header.stamp = stamp
        joints.name = list(JOINTS)
        joints.position = list(self._wheel_angle)
        joints.velocity = wheel_speeds
        self.joint_pub.publish(joints)

    def _publish_status(self):
        now = time.monotonic()
        stamp = self.get_clock().now().to_msg()
        b = self.board
        with self._lock:
            status, status_time = b.status, b.status_time
            rpm, current, ticks = b.rpm, b.current, b.ticks
            safety, safety_online = self._safety, self._safety_online(now)
            estop_requested = self._estop_requested

        # ── e-stop controller ────────────────────────────────────────
        msg = SafetyStatus()
        msg.header.stamp = stamp
        msg.online = safety_online
        if safety is not None:
            msg.estop_active = safety.estop_active
            msg.hoverboard_power = safety.hoverboard_power
            msg.button_pressed = safety.button_pressed
            msg.cause = safety.cause
            msg.heartbeat = safety.heartbeat
        self.safety_pub.publish(msg)

        if not safety_online:
            self.get_logger().warning(
                "No status from the e-stop controller (STM32F411, frame 0x011): "
                "only the hoverboard's own e-stop is available", throttle_duration_sec=30.0)
        estop = (safety.estop_active if safety_online else estop_requested)
        if estop != self._estop_logged:
            if estop:
                cause = CAUSES.get(safety.cause, "?") if safety_online else "host request"
                self.get_logger().warning(f"E-STOP ACTIVE (cause: {cause})")
            elif self._estop_logged is not None:
                self.get_logger().info("E-stop released")
            self._estop_logged = estop

        # ── hoverboard ───────────────────────────────────────────────
        online = status_time is not None and now - status_time < self.status_timeout
        if online and not self._board_online:
            self.get_logger().info(f"Hoverboard (CAN id {b.node_id}) online")
        self._board_online = online
        unpowered = safety_online and not safety.hoverboard_power
        if not online and not unpowered:
            self.get_logger().warning(
                f"No status from the hoverboard (CAN id {b.node_id})", throttle_duration_sec=5.0)

        board = HoverboardStatus()
        board.header.stamp = stamp
        board.name = "drive"
        board.node_id = b.node_id
        board.online = online
        board.rpm_left, board.rpm_right = rpm
        board.current_left, board.current_right = current
        if ticks is not None:
            board.ticks_left, board.ticks_right = ticks
        if status is not None:
            board.battery_voltage = status.battery_voltage
            board.battery_percent = status.battery_percent
            board.board_temperature = status.board_temperature
            board.error_left = status.error_left
            board.error_right = status.error_right
            board.motors_enabled = status.motors_enabled
            board.cmd_timeout = status.cmd_timeout
            board.safe_state = status.safe_state
            board.estop_active = status.estop_active
            board.aux_input = status.aux_input
            board.battery_low = status.battery_low
            board.temp_warning = status.temp_warning
            board.can_error_counter = status.can_error_counter
            board.heartbeat = status.heartbeat
            if online and (status.error_left or status.error_right
                           or status.battery_low or status.temp_warning):
                self.get_logger().warning(
                    f"Hoverboard faults: err L={status.error_left} R={status.error_right}"
                    f"{' battery-low' if status.battery_low else ''}"
                    f"{' over-temp' if status.temp_warning else ''}",
                    throttle_duration_sec=5.0)
        self.board_pub.publish(board)

    def destroy_node(self):
        self._notifier.stop()
        self._send(hb.cmd_id(self.board.node_id), hb.encode_cmd(0, 0))
        self.bus.shutdown()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    try:
        node = CanBridge()
    except (OSError, can.CanError) as e:
        rclpy.logging.get_logger("can_bridge").fatal(
            f"Cannot open the CAN interface: {e}. See docs/hardware.md (CAN bus).")
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
