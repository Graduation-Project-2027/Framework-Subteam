"""AMR CAN protocol (hoverboard VARIANT_CAN + e-stop controller) and diff-drive kinematics.

Pure Python (no ROS imports) so it can be unit-tested off the robot.
The frame layout mirrors Hoverboard_Firmware/docs/hoverboard_can.dbc:
standard 11-bit IDs, little-endian payloads, n = CAN_NODE_ID of the board.
Every wheel value uses the firmware sign convention: positive = robot forward.
"""

import math
import struct
from dataclasses import dataclass

ESTOP_ID = 0x010
SAFETY_STATUS_ID = 0x011             # sent by the STM32F411 e-stop controller
CMD_BASE = 0x200
ODOM_BASE = 0x180
MOTION_BASE = 0x280
STATUS_BASE = 0x380

_CMD = struct.Struct("<hh")          # rpm_left, rpm_right
_ODOM = struct.Struct("<ii")         # ticks_left, ticks_right
_MOTION = struct.Struct("<hhhh")     # rpm_left, rpm_right, current_left*100, current_right*100
_STATUS = struct.Struct("<HBbBBBB")  # batV*100, bat%, temp, err, flags, can_err, heartbeat
_SAFETY = struct.Struct("<BB")       # flags (bits 0-2) + cause (bits 4-6), heartbeat

CAUSE_NONE = 0
CAUSE_HOST_REQUEST = 1
CAUSE_BUTTON = 2
CAUSE_HOST_TIMEOUT = 3

INT16_MIN = -32768
INT16_MAX = 32767


def cmd_id(node_id):
    return CMD_BASE + node_id


def odom_id(node_id):
    return ODOM_BASE + node_id


def motion_id(node_id):
    return MOTION_BASE + node_id


def status_id(node_id):
    return STATUS_BASE + node_id


@dataclass
class Status:
    battery_voltage: float
    battery_percent: int
    board_temperature: int
    error_left: int
    error_right: int
    motors_enabled: bool
    cmd_timeout: bool
    safe_state: bool
    estop_active: bool
    aux_input: bool
    battery_low: bool
    temp_warning: bool
    can_error_counter: int
    heartbeat: int


@dataclass
class SafetyStatus:
    estop_active: bool
    hoverboard_power: bool
    button_pressed: bool
    cause: int
    heartbeat: int


def encode_cmd(rpm_left, rpm_right):
    """HB_CMD payload. Values are rpm in the firmware's SPD_MODE."""
    clamp = lambda v: max(INT16_MIN, min(INT16_MAX, int(round(v))))
    return _CMD.pack(clamp(rpm_left), clamp(rpm_right))


def encode_estop(active):
    """ESTOP payload: 1 = stop (latched on every node), 0 = release."""
    return bytes([1 if active else 0])


def decode_odom(data):
    """HB_ODOM -> (ticks_left, ticks_right)."""
    return _ODOM.unpack_from(data)


def decode_motion(data):
    """HB_MOTION -> (rpm_left, rpm_right, current_left [A], current_right [A])."""
    rpm_l, rpm_r, cur_l, cur_r = _MOTION.unpack_from(data)
    return rpm_l, rpm_r, cur_l / 100.0, cur_r / 100.0


def decode_status(data):
    bat_v, bat_pct, temp, err, flags, can_err, heartbeat = _STATUS.unpack_from(data)
    return Status(
        battery_voltage=bat_v / 100.0,
        battery_percent=bat_pct,
        board_temperature=temp,
        error_left=err & 0x0F,
        error_right=(err >> 4) & 0x0F,
        motors_enabled=bool(flags & 0x01),
        cmd_timeout=bool(flags & 0x02),
        safe_state=bool(flags & 0x04),
        estop_active=bool(flags & 0x08),
        aux_input=bool(flags & 0x10),
        battery_low=bool(flags & 0x20),
        temp_warning=bool(flags & 0x40),
        can_error_counter=can_err,
        heartbeat=heartbeat,
    )


def decode_safety_status(data):
    """SAFETY_STATUS of the e-stop controller (relay that powers the hoverboard board)."""
    flags, heartbeat = _SAFETY.unpack_from(data)
    return SafetyStatus(
        estop_active=bool(flags & 0x01),
        hoverboard_power=bool(flags & 0x02),
        button_pressed=bool(flags & 0x04),
        cause=(flags >> 4) & 0x07,
        heartbeat=heartbeat,
    )


def tick_delta(new, old):
    """Difference of two int32 hall counters, robust to wrap-around."""
    d = (new - old) & 0xFFFFFFFF
    return d - (1 << 32) if d >= (1 << 31) else d


def rpm_to_rad_s(rpm):
    return rpm * 2.0 * math.pi / 60.0


def rad_s_to_rpm(w):
    return w * 60.0 / (2.0 * math.pi)


class DiffDriveKinematics:
    """Two driven wheels on one axle through the robot centre (casters elsewhere).

    Wheel speeds are angular [rad/s], positive when the wheel rolls forward.
    """

    def __init__(self, wheel_radius, wheel_separation):
        self.r = wheel_radius
        self.half_track = wheel_separation / 2.0

    def inverse(self, v, wz):
        """Body twist -> (left, right) wheel angular speeds."""
        return (
            (v - wz * self.half_track) / self.r,
            (v + wz * self.half_track) / self.r,
        )

    def forward(self, left, right):
        """Wheel speeds (or angle increments) -> (v, wz) (or (distance, heading change))."""
        return (
            self.r * (left + right) / 2.0,
            self.r * (right - left) / (2.0 * self.half_track),
        )
