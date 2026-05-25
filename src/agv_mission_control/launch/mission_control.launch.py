"""Launch mission manager + rosbridge websocket server."""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, ExecuteProcess, TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("agv_mission_control")
    
    # Try to find the GUI source directory relative to the package share
    # This works if we are in a colcon workspace
    pkg_share_path = get_package_share_directory("agv_mission_control")
    # share/agv_mission_control -> ../../../src/agv_mission_control/gui
    gui_src_path = os.path.join(pkg_share_path, "..", "..", "..", "src", "agv_mission_control", "gui")
    
    # If the above path doesn't exist (e.g. installed without src), 
    # we can't run 'npm run dev' from here easily, but we'll try a fallback.
    if not os.path.isdir(gui_src_path):
        # Fallback to local path relative to current working directory
        gui_src_path = os.path.abspath("src/agv_mission_control/gui")

    # Empty default → mission_manager uses ~/.ros/agv_locations.json,
    # which survives colcon rebuilds.  The install-copy version of
    # config/locations.json was getting clobbered every build, wiping
    # any GUI-saved lanes/racks.  Pass an explicit path if you want
    # to override.
    return LaunchDescription([
        DeclareLaunchArgument(
            "use_sim_time", default_value="true",
            description="Use simulation clock",
        ),
        DeclareLaunchArgument(
            "locations_file", default_value="",
            description="Path to rack/dock locations JSON "
                        "(empty → ~/.ros/agv_locations.json)",
        ),
        DeclareLaunchArgument(
            "rosbridge_port", default_value="9090",
            description="WebSocket port for rosbridge",
        ),
        DeclareLaunchArgument(
            "start_gui", default_value="true",
            description="Whether to start the Vite dev server automatically",
        ),
        DeclareLaunchArgument(
            "open_browser", default_value="true",
            description="Whether to open the browser automatically",
        ),

        # ── Mission Manager ──────────────────────────────────────────
        Node(
            package="agv_mission_control",
            executable="mission_manager",
            name="mission_manager",
            output="screen",
            parameters=[
                os.path.join(pkg, "config", "mission_manager.yaml"),
                {
                    "use_sim_time": LaunchConfiguration("use_sim_time"),
                    "locations_file": LaunchConfiguration("locations_file"),
                },
            ],
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

        # ── Vite GUI Server ──────────────────────────────────────────
        # Runs 'npm run dev' in the background
        ExecuteProcess(
            condition=IfCondition(LaunchConfiguration("start_gui")),
            cmd=["npm", "run", "dev", "--", "--host"],
            cwd=gui_src_path,
            output="screen",
            shell=True
        ),

        # ── Open Browser ─────────────────────────────────────────────
        # Wait a few seconds for the dev server to warm up before opening
        TimerAction(
            period=3.0,
            actions=[
                ExecuteProcess(
                    condition=IfCondition(LaunchConfiguration("open_browser")),
                    cmd=["xdg-open", "http://localhost:5173"],
                    output="screen"
                )
            ]
        ),

        LogInfo(msg="═══════════════════════════════════════════════════"),
        LogInfo(msg="  Mission Control GUI: http://localhost:5173"),
        LogInfo(msg="  Rosbridge WS:        ws://localhost:9090"),
        LogInfo(msg="═══════════════════════════════════════════════════"),
    ])
