"""Real-robot drivers on the Raspberry Pi 5.

2x RPLidar A1M8 (/scan, /scan_rear), MPU9250 + Madgwick filter (/imu/data) and
the CAN bridge (hoverboard drive board + STM32F411 e-stop controller). Included by
amr_bringup/bringup.launch.py when sim:=false; parameters in config/hardware.yaml.
"""

from launch import LaunchDescription
from launch.substitutions import PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    config = PathJoinSubstitution([FindPackageShare("amr_hardware"), "config", "hardware.yaml"])

    return LaunchDescription([
        Node(
            package="rplidar_ros",
            executable="rplidar_node",
            name="rplidar",
            namespace="front_lidar",
            parameters=[config],
            remappings=[("scan", "/scan")],
            output="screen",
        ),
        Node(
            package="rplidar_ros",
            executable="rplidar_node",
            name="rplidar",
            namespace="rear_lidar",
            parameters=[config],
            remappings=[("scan", "/scan_rear")],
            output="screen",
        ),
        Node(
            package="amr_hardware",
            executable="mpu9250_driver",
            name="mpu9250",
            parameters=[config],
            output="screen",
        ),
        Node(
            package="imu_filter_madgwick",
            executable="imu_filter_madgwick_node",
            name="imu_filter",
            parameters=[config],
            output="screen",
        ),
        Node(
            package="amr_hardware",
            executable="can_bridge",
            name="can_bridge",
            parameters=[config],
            output="screen",
        ),
    ])
