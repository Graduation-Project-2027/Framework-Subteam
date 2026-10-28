"""Dual-lidar scan pipeline shared by mapping and navigation.

/scan, /scan_rear -> laser_filters (range + field of view) -> /scan_{front,rear}_filtered
  -> payload_filter (drops the carried rack's legs) -> /scan_{front,rear}_nav
  -> scan_merger -> /scan_merged (360 deg, used by slam_toolbox)
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    use_sim_time = LaunchConfiguration("use_sim_time")
    filters = PathJoinSubstitution([FindPackageShare("amr_mapping"), "config", "laser_filters.yaml"])

    return LaunchDescription([
        DeclareLaunchArgument("use_sim_time", default_value="false"),
        Node(
            package="laser_filters",
            executable="scan_to_scan_filter_chain",
            name="scan_filter_front",
            output="screen",
            parameters=[filters, {"use_sim_time": use_sim_time}],
            remappings=[("scan", "/scan"), ("scan_filtered", "/scan_front_filtered")],
        ),
        Node(
            package="laser_filters",
            executable="scan_to_scan_filter_chain",
            name="scan_filter_rear",
            output="screen",
            parameters=[filters, {"use_sim_time": use_sim_time}],
            remappings=[("scan", "/scan_rear"), ("scan_filtered", "/scan_rear_filtered")],
        ),
        Node(
            package="amr_mapping",
            executable="payload_filter.py",
            name="payload_filter",
            output="screen",
            parameters=[{"use_sim_time": use_sim_time}],
        ),
        # Merges the payload-filtered scans so SLAM never maps the carried rack's legs.
        Node(
            package="amr_mapping",
            executable="scan_merger.py",
            name="scan_merger",
            output="screen",
            parameters=[{
                "use_sim_time": use_sim_time,
                "front_topic": "/scan_front_nav",
                "rear_topic": "/scan_rear_nav",
            }],
        ),
    ])
