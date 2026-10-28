#!/usr/bin/env python3
"""Carried-payload laser self-filter.

When the AMR is carrying a rack, the rack's 4 legs sit in the lidar plane and
move rigidly with the robot.  Left alone they get marked as obstacles in the
costmap and, because they occlude the space behind them, leave an un-clearable
trail that boxes the robot in.

This node removes ONLY the returns that fall within a small radius of the known
carried-leg positions (published by the mission manager in the base frame),
and passes everything else through untouched.  When not carrying, it is a pure
pass-through.  Works identically in sim and on the real robot — it does not
rely on lifting the legs out of the scanner.

Subscribes:
  <in_front>, <in_rear>   (sensor_msgs/LaserScan)  filtered scans
  /payload/leg_mask       (std_msgs/Float32MultiArray)
                          flat [x0,y0,x1,y1,...] leg positions in base_frame;
                          empty array = not carrying (pass-through)
Publishes:
  <out_front>, <out_rear> (sensor_msgs/LaserScan)  payload-free scans
"""

import math
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data, QoSProfile, \
    ReliabilityPolicy, DurabilityPolicy

from sensor_msgs.msg import LaserScan
from std_msgs.msg import Float32MultiArray

import tf2_ros


class PayloadFilter(Node):
    def __init__(self):
        super().__init__("payload_filter")

        self.declare_parameter("base_frame", "base_footprint")
        # Margin added around the carried legs' bounding box (m).  We mask a
        # single rectangle covering the whole rack footprint rather than 4
        # circles — robust to small leg shifts / lidar grazing, and since the
        # robot is UNDER the rack nothing real can be inside that box.
        self.declare_parameter("box_margin", 0.12)
        self.declare_parameter("in_front", "/scan_front_filtered")
        self.declare_parameter("in_rear", "/scan_rear_filtered")
        self.declare_parameter("out_front", "/scan_front_nav")
        self.declare_parameter("out_rear", "/scan_rear_nav")
        self.declare_parameter("mask_topic", "/payload/leg_mask")

        self.base_frame = self.get_parameter("base_frame").value
        self.box_margin = float(self.get_parameter("box_margin").value)

        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # Mask box in base_frame (xmin, xmax, ymin, ymax); None ⇒ inactive.
        self._box = None

        # Mask is latched by the publisher (transient local) so we pick up
        # the current carry state immediately even if we start late.
        mask_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self.create_subscription(
            Float32MultiArray, self.get_parameter("mask_topic").value,
            self._mask_cb, mask_qos)

        self._pub_front = self.create_publisher(
            LaserScan, self.get_parameter("out_front").value,
            qos_profile_sensor_data)
        self._pub_rear = self.create_publisher(
            LaserScan, self.get_parameter("out_rear").value,
            qos_profile_sensor_data)

        self.create_subscription(
            LaserScan, self.get_parameter("in_front").value,
            lambda m: self._scan_cb(m, self._pub_front),
            qos_profile_sensor_data)
        self.create_subscription(
            LaserScan, self.get_parameter("in_rear").value,
            lambda m: self._scan_cb(m, self._pub_rear),
            qos_profile_sensor_data)

        self.get_logger().info(
            f"PayloadFilter up (box_margin={self.box_margin:.2f} m). "
            "Pass-through until a non-empty /payload/leg_mask arrives.")

    def _mask_cb(self, msg: Float32MultiArray):
        data = list(msg.data)
        if len(data) >= 2:
            legs = np.array(data, dtype=np.float64).reshape(-1, 2)
            m = self.box_margin
            self._box = (legs[:, 0].min() - m, legs[:, 0].max() + m,
                         legs[:, 1].min() - m, legs[:, 1].max() + m)
            self.get_logger().info(
                f"Payload mask ACTIVE: box x[{self._box[0]:.2f},"
                f"{self._box[1]:.2f}] y[{self._box[2]:.2f},{self._box[3]:.2f}] "
                f"({len(legs)} legs)")
        else:
            if self._box is not None:
                self.get_logger().info("Payload mask cleared (pass-through).")
            self._box = None

    def _scan_cb(self, scan: LaserScan, pub):
        # Inactive → pure pass-through (no copy, no TF cost).
        if self._box is None:
            pub.publish(scan)
            return

        try:
            tf = self.tf_buffer.lookup_transform(
                self.base_frame, scan.header.frame_id, rclpy.time.Time())
        except Exception:
            # Can't relate frames → safest is to pass through unmodified.
            pub.publish(scan)
            return

        q = tf.transform.rotation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                         1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        tx = tf.transform.translation.x
        ty = tf.transform.translation.y
        c, s = math.cos(yaw), math.sin(yaw)

        ranges = np.asarray(scan.ranges, dtype=np.float64)
        n = ranges.shape[0]
        angles = scan.angle_min + np.arange(n) * scan.angle_increment
        finite = np.isfinite(ranges)

        # Beam endpoints in base frame.
        xs = ranges * np.cos(angles)
        ys = ranges * np.sin(angles)
        xb = c * xs - s * ys + tx
        yb = s * xs + c * ys + ty

        # Mark beams landing inside the carried-rack box.
        xmin, xmax, ymin, ymax = self._box
        masked = (xb >= xmin) & (xb <= xmax) & (yb >= ymin) & (yb <= ymax)
        masked &= finite

        if not masked.any():
            pub.publish(scan)
            return

        out = LaserScan()
        out.header = scan.header
        out.angle_min = scan.angle_min
        out.angle_max = scan.angle_max
        out.angle_increment = scan.angle_increment
        out.time_increment = scan.time_increment
        out.scan_time = scan.scan_time
        out.range_min = scan.range_min
        out.range_max = scan.range_max
        new_ranges = ranges.copy()
        new_ranges[masked] = float("inf")
        out.ranges = new_ranges.astype(np.float32).tolist()
        out.intensities = scan.intensities
        pub.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = PayloadFilter()
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
