"""Launch mission manager + rosbridge websocket server."""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("agv_mission_control")

    locations_file = os.path.join(pkg, "config", "locations.json")

    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time", default_value="true",
            description="Use simulation clock",
        ),
        DeclareLaunchArgument(
            "locations_file", default_value=locations_file,
            description="Path to rack/dock locations JSON",
        ),
        DeclareLaunchArgument(
            "rosbridge_port", default_value="9090",
            description="WebSocket port for rosbridge",
        ),

        # ── Mission Manager ──────────────────────────────────────────
        Node(
            package="agv_mission_control",
            executable="mission_manager",
            name="mission_manager",
            output="screen",
            parameters=[{
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "locations_file": LaunchConfiguration("locations_file"),
            }],
        ),

        # ── Rosbridge WebSocket ──────────────────────────────────────
        Node(
            package="rosbridge_server",
            executable="rosbridge_websocket",
            name="rosbridge_websocket",
            output="screen",
            parameters=[{
                "port": LaunchConfiguration("rosbridge_port"),
                "use_sim_time": LaunchConfiguration("use_sim_time"),
            }],
        ),

        LogInfo(msg="═══════════════════════════════════════════════════"),
        LogInfo(msg="  Mission Control GUI: http://localhost:5173"),
        LogInfo(msg="  Rosbridge WS:        ws://localhost:9090"),
        LogInfo(msg="═══════════════════════════════════════════════════"),
    ])
