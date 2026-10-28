#!/usr/bin/env python3

import math
import os
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass
from typing import Dict, Set

import fcntl
import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rich import box
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from std_msgs.msg import Float64


@dataclass
class OdomState:
    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    linear_x: float = 0.0
    linear_y: float = 0.0
    angular_z: float = 0.0
    last_update_monotonic: float = 0.0


class TerminalKeyboard:
    TRACKED_KEYS = {"w", "a", "s", "d", "q", "e", "u", "i"}
    EVENT_KEYS = {"[", "/"}

    def __init__(self) -> None:
        self._fd = None
        self._old_termios = None
        self._old_flags = None
        self._last_press: Dict[str, float] = {}
        self._events = []
        self.quit_requested = False

    def __enter__(self):
        if not sys.stdin.isatty():
            raise RuntimeError(
                "Keyboard input requires running in a real terminal (TTY)."
            )

        self._fd = sys.stdin.fileno()
        self._old_termios = termios.tcgetattr(self._fd)
        self._old_flags = fcntl.fcntl(self._fd, fcntl.F_GETFL)

        tty.setcbreak(self._fd)
        fcntl.fcntl(self._fd, fcntl.F_SETFL, self._old_flags | os.O_NONBLOCK)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fd is not None and self._old_termios is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)
        if self._fd is not None and self._old_flags is not None:
            fcntl.fcntl(self._fd, fcntl.F_SETFL, self._old_flags)

    def poll(self) -> None:
        if self._fd is None:
            return

        while True:
            ready, _, _ = select.select([self._fd], [], [], 0.0)
            if not ready:
                break

            try:
                payload = os.read(self._fd, 128)
            except BlockingIOError:
                break

            if not payload:
                break

            for char in payload.decode("utf-8", errors="ignore"):
                self._handle_char(char)

    def active_keys(self, hold_timeout: float) -> Set[str]:
        now = time.monotonic()
        stale = [k for k, t in self._last_press.items() if (now - t) > hold_timeout]
        for key in stale:
            self._last_press.pop(key, None)
        return set(self._last_press.keys())

    def consume_events(self):
        events = self._events
        self._events = []
        return events

    def _handle_char(self, char: str) -> None:
        if char == "\x03":
            raise KeyboardInterrupt
        if char == "\x11":
            self.quit_requested = True
            return
        if char == " ":
            self._last_press.clear()
            return

        key = char.lower()
        if key in self.TRACKED_KEYS:
            self._last_press[key] = time.monotonic()
        elif key in self.EVENT_KEYS:
            self._events.append(key)


class RichTeleopNode(Node):
    def __init__(self) -> None:
        super().__init__("rich_teleop_tui")

        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("lift_cmd_topic", "/lift_joint/cmd_pos")
        self.declare_parameter("linear_speed", 1.5)
        self.declare_parameter("strafe_speed", 0.5)
        self.declare_parameter("angular_speed", 1.0)
        self.declare_parameter("lift_step", 0.01)
        self.declare_parameter("lift_min", 0.0)
        self.declare_parameter("lift_max", 0.05)   # new 5 cm physical stroke
        self.declare_parameter("linear_speed_step", 0.1)
        self.declare_parameter("linear_speed_min", 0.1)
        self.declare_parameter("linear_speed_max", 3.0)
        self.declare_parameter("key_hold_timeout", 0.6)

        odom_topic = self.get_parameter("odom_topic").value
        cmd_vel_topic = self.get_parameter("cmd_vel_topic").value
        lift_cmd_topic = self.get_parameter("lift_cmd_topic").value

        self.linear_speed = float(self.get_parameter("linear_speed").value)
        self.strafe_speed = float(self.get_parameter("strafe_speed").value)
        self.angular_speed = float(self.get_parameter("angular_speed").value)
        self.lift_step = float(self.get_parameter("lift_step").value)
        self.lift_min = float(self.get_parameter("lift_min").value)
        self.lift_max = float(self.get_parameter("lift_max").value)
        self.linear_speed_step = float(self.get_parameter("linear_speed_step").value)
        self.linear_speed_min = float(self.get_parameter("linear_speed_min").value)
        self.linear_speed_max = float(self.get_parameter("linear_speed_max").value)
        self.key_hold_timeout = float(self.get_parameter("key_hold_timeout").value)

        self.cmd_pub = self.create_publisher(Twist, cmd_vel_topic, 10)
        self.lift_pub = self.create_publisher(Float64, lift_cmd_topic, 10)
        self.odom_sub = self.create_subscription(
            Odometry, odom_topic, self._odom_cb, 10
        )

        self.odom_state = OdomState()
        self.cmd_x = 0.0
        self.cmd_y = 0.0
        self.cmd_wz = 0.0
        self.lift_position = self.lift_min

        self.get_logger().info("Rich teleop TUI started. Ctrl+C to exit.")

    def _odom_cb(self, msg: Odometry) -> None:
        q = msg.pose.pose.orientation
        siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)

        self.odom_state.x = msg.pose.pose.position.x
        self.odom_state.y = msg.pose.pose.position.y
        self.odom_state.yaw = math.atan2(siny_cosp, cosy_cosp)
        self.odom_state.linear_x = msg.twist.twist.linear.x
        self.odom_state.linear_y = msg.twist.twist.linear.y
        self.odom_state.angular_z = msg.twist.twist.angular.z
        self.odom_state.last_update_monotonic = time.monotonic()

    def publish_from_keys(self, active_keys: Set[str], key_events) -> None:
        for event in key_events:
            if event == "[":
                self.linear_speed = max(
                    self.linear_speed_min,
                    self.linear_speed - self.linear_speed_step,
                )
            elif event == "/":
                self.linear_speed = min(
                    self.linear_speed_max,
                    self.linear_speed + self.linear_speed_step,
                )

        cmd_x = 0.0
        cmd_y = 0.0
        cmd_wz = 0.0

        if "w" in active_keys:
            cmd_x += self.linear_speed
        if "s" in active_keys:
            cmd_x -= self.linear_speed
        if "a" in active_keys:
            cmd_y += self.strafe_speed
        if "d" in active_keys:
            cmd_y -= self.strafe_speed
        if "q" in active_keys:
            cmd_wz += self.angular_speed
        if "e" in active_keys:
            cmd_wz -= self.angular_speed

        self.cmd_x = cmd_x
        self.cmd_y = cmd_y
        self.cmd_wz = cmd_wz

        twist = Twist()
        twist.linear.x = cmd_x
        twist.linear.y = cmd_y
        twist.angular.z = cmd_wz
        self.cmd_pub.publish(twist)

        lift_delta = 0.0
        if "u" in active_keys:
            lift_delta += self.lift_step
        if "i" in active_keys:
            lift_delta -= self.lift_step

        if abs(lift_delta) > 1e-12:
            self.lift_position = max(
                self.lift_min,
                min(self.lift_max, self.lift_position + lift_delta),
            )
            lift_msg = Float64()
            lift_msg.data = self.lift_position
            self.lift_pub.publish(lift_msg)

    def publish_stop(self) -> None:
        self.cmd_x = 0.0
        self.cmd_y = 0.0
        self.cmd_wz = 0.0
        self.cmd_pub.publish(Twist())

    def render(self, active_keys: Set[str]):
        now = time.monotonic()
        odom_age = now - self.odom_state.last_update_monotonic
        odom_status = "[green]LIVE[/green]" if odom_age < 1.0 else "[red]STALE[/red]"

        odom_table = Table(box=box.SIMPLE_HEAVY)
        odom_table.add_column("Field", style="bold cyan")
        odom_table.add_column("Value", justify="right")
        odom_table.add_row("Status", odom_status)
        odom_table.add_row(r"x \[m]", f"{self.odom_state.x:.3f}")
        odom_table.add_row(r"y \[m]", f"{self.odom_state.y:.3f}")
        odom_table.add_row(r"yaw \[rad]", f"{self.odom_state.yaw:.3f}")
        odom_table.add_row(r"vx \[m/s]", f"{self.odom_state.linear_x:.3f}")
        odom_table.add_row(r"vy \[m/s]", f"{self.odom_state.linear_y:.3f}")
        odom_table.add_row(r"wz \[rad/s]", f"{self.odom_state.angular_z:.3f}")
        odom_panel = Panel(odom_table, title="Odometry", border_style="cyan")

        cmd_table = Table(box=box.SIMPLE_HEAVY)
        cmd_table.add_column("Command", style="bold magenta")
        cmd_table.add_column("Value", justify="right")
        cmd_table.add_row(r"cmd_vel.x \[m/s]", f"{self.cmd_x:.3f}")
        cmd_table.add_row(r"cmd_vel.y \[m/s]", f"{self.cmd_y:.3f}")
        cmd_table.add_row(r"cmd_vel.z \[rad/s]", f"{self.cmd_wz:.3f}")
        cmd_table.add_row(r"linear speed \[m/s]", f"{self.linear_speed:.2f}")
        cmd_table.add_row(r"lift_cmd \[m]", f"{self.lift_position:.3f}")

        key_line = "  ".join(
            [
                self._key_badge("w", active_keys),
                self._key_badge("a", active_keys),
                self._key_badge("s", active_keys),
                self._key_badge("d", active_keys),
                self._key_badge("q", active_keys),
                self._key_badge("e", active_keys),
                self._key_badge("u", active_keys),
                self._key_badge("i", active_keys),
            ]
        )

        controls_table = Table.grid(padding=(0, 1))
        controls_table.add_row(
            r"[bold]Controls[/bold]: W/A/S/D move, Q/E rotate, U/I lift, \[/] speed"
        )
        controls_table.add_row(
            r"[bold]Speed[/bold]: \[ decreases, / increases linear speed"
        )
        controls_table.add_row("[bold]Space[/bold]: release all keys")
        controls_table.add_row("[bold]Quit[/bold]: Ctrl+C (or Ctrl+Q)")
        controls_table.add_row(f"[bold]Active[/bold]: {key_line}")

        right_panel = Panel(cmd_table, title="Control", border_style="magenta")

        controls_panel = Panel(controls_table, border_style="green")

        layout = Layout()
        layout.split_column(
            Layout(name="top", ratio=3),
            Layout(name="bottom", ratio=2),
        )
        layout["top"].split_row(
            Layout(odom_panel, ratio=1), Layout(right_panel, ratio=1)
        )
        layout["bottom"].update(controls_panel)
        return layout

    @staticmethod
    def _key_badge(key: str, active_keys: Set[str]) -> str:
        if key in active_keys:
            return f"[black on green] {key.upper()} [/]"
        return f"[white on grey23] {key.upper()} [/]"


def main() -> None:
    rclpy.init()
    node = RichTeleopNode()

    try:
        with TerminalKeyboard() as keyboard:
            with Live(node.render(set()), refresh_per_second=15, screen=True) as live:
                while rclpy.ok() and not keyboard.quit_requested:
                    keyboard.poll()
                    active_keys = keyboard.active_keys(node.key_hold_timeout)
                    key_events = keyboard.consume_events()
                    node.publish_from_keys(active_keys, key_events)
                    rclpy.spin_once(node, timeout_sec=0.01)
                    live.update(node.render(active_keys))
                    time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    except RuntimeError as err:
        node.get_logger().error(str(err))
    finally:
        node.publish_stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
