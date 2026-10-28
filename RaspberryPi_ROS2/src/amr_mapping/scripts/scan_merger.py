#!/usr/bin/env python3
"""Merge two filtered LaserScan topics into a single scan.

Subscribes to two LaserScan topics, projects both into a common target frame,
and publishes a unified LaserScan covering the full 360° around the robot.
"""

import math
import numpy as np

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data

from sensor_msgs.msg import LaserScan

import tf2_ros


class ScanMerger(Node):
    """Merge front and rear filtered laser scans into one 360° scan."""

    def __init__(self):
        super().__init__("scan_merger")

        # Parameters
        self.declare_parameter("target_frame", "laser_frame")
        self.declare_parameter("merged_topic", "/scan_merged")
        self.declare_parameter("front_topic", "/scan_front_filtered")
        self.declare_parameter("rear_topic", "/scan_rear_filtered")
        self.declare_parameter("angle_min", -math.pi)
        self.declare_parameter("angle_max", math.pi)
        self.declare_parameter("angle_increment", 0.00872665)  # ~0.5°, 720 samples
        self.declare_parameter("range_min", 0.15)
        self.declare_parameter("range_max", 30.0)

        self.target_frame = self.get_parameter("target_frame").value
        merged_topic = self.get_parameter("merged_topic").value
        front_topic = self.get_parameter("front_topic").value
        rear_topic = self.get_parameter("rear_topic").value
        self.out_angle_min = self.get_parameter("angle_min").value
        self.out_angle_max = self.get_parameter("angle_max").value
        self.out_angle_inc = self.get_parameter("angle_increment").value
        self.out_range_min = self.get_parameter("range_min").value
        self.out_range_max = self.get_parameter("range_max").value

        # TF
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        # Subscribers
        self.front_scan = None
        self.rear_scan = None

        self.create_subscription(
            LaserScan, front_topic, self._front_cb, qos_profile_sensor_data
        )
        self.create_subscription(
            LaserScan, rear_topic, self._rear_cb, qos_profile_sensor_data
        )

        # Publisher
        self.merged_pub = self.create_publisher(
            LaserScan, merged_topic, qos_profile_sensor_data
        )

        self.get_logger().info(
            f"ScanMerger: {front_topic} + {rear_topic} → {merged_topic} "
            f"(frame={self.target_frame})"
        )

    # ------------------------------------------------------------------
    def _front_cb(self, msg: LaserScan):
        self.front_scan = msg
        self._try_merge()

    def _rear_cb(self, msg: LaserScan):
        self.rear_scan = msg
        self._try_merge()

    # ------------------------------------------------------------------
    def _try_merge(self):
        if self.front_scan is None or self.rear_scan is None:
            return

        # Use the most recent timestamp
        stamp = max(
            self.front_scan.header.stamp.sec * 1_000_000_000
            + self.front_scan.header.stamp.nanosec,
            self.rear_scan.header.stamp.sec * 1_000_000_000
            + self.rear_scan.header.stamp.nanosec,
        )

        # Build output ranges array (all inf = no reading)
        n_bins = int(
            round((self.out_angle_max - self.out_angle_min) / self.out_angle_inc)
        )
        ranges = np.full(n_bins, float("inf"), dtype=np.float32)

        for scan in (self.front_scan, self.rear_scan):
            self._project_scan(scan, ranges)

        # Publish
        out = LaserScan()
        out.header.stamp.sec = int(stamp // 1_000_000_000)
        out.header.stamp.nanosec = int(stamp % 1_000_000_000)
        out.header.frame_id = self.target_frame
        out.angle_min = self.out_angle_min
        out.angle_max = self.out_angle_max
        out.angle_increment = self.out_angle_inc
        out.range_min = self.out_range_min
        out.range_max = self.out_range_max
        out.time_increment = 0.0
        out.scan_time = 0.0
        out.ranges = ranges.tolist()
        out.intensities = []

        self.merged_pub.publish(out)

        # Clear to avoid re-publishing stale data
        self.front_scan = None
        self.rear_scan = None

    # ------------------------------------------------------------------
    def _project_scan(self, scan: LaserScan, ranges: np.ndarray):
        """Project a single LaserScan into the merged ranges array."""
        try:
            tf = self.tf_buffer.lookup_transform(
                self.target_frame, scan.header.frame_id, rclpy.time.Time()
            )
        except (
            tf2_ros.LookupException,
            tf2_ros.ConnectivityException,
            tf2_ros.ExtrapolationException,
        ) as e:
            self.get_logger().warn(
                f"TF lookup {scan.header.frame_id} → {self.target_frame} failed: {e}",
                throttle_duration_sec=2.0,
            )
            return

        # Extract yaw from quaternion (2D only)
        q = tf.transform.rotation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
        )
        tx = tf.transform.translation.x
        ty = tf.transform.translation.y

        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)

        for i, r in enumerate(scan.ranges):
            if r < scan.range_min or r > scan.range_max or not math.isfinite(r):
                continue

            # Angle in the source frame
            angle_src = scan.angle_min + i * scan.angle_increment

            # Cartesian in source frame
            x_src = r * math.cos(angle_src)
            y_src = r * math.sin(angle_src)

            # Transform to target frame
            x_tgt = cos_yaw * x_src - sin_yaw * y_src + tx
            y_tgt = sin_yaw * x_src + cos_yaw * y_src + ty

            # Polar in target frame
            r_tgt = math.sqrt(x_tgt * x_tgt + y_tgt * y_tgt)
            angle_tgt = math.atan2(y_tgt, x_tgt)

            if angle_tgt < self.out_angle_min or angle_tgt > self.out_angle_max:
                continue
            if r_tgt < self.out_range_min or r_tgt > self.out_range_max:
                continue

            # Bin index
            idx = int(round((angle_tgt - self.out_angle_min) / self.out_angle_inc))
            if 0 <= idx < len(ranges):
                # Keep the closest reading per bin
                if r_tgt < ranges[idx]:
                    ranges[idx] = r_tgt


def main(args=None):
    rclpy.init(args=args)
    node = ScanMerger()
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
