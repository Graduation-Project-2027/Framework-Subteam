"""Launch mission manager + rosbridge websocket server (+ the GUI dev server).

    ros2 launch amr_mission_control mission_control.launch.py              # real robot
    ros2 launch amr_mission_control mission_control.launch.py sim:=true    # with the Gazebo stack
"""

import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, ExecuteProcess, TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("amr_mission_control")
    sim = LaunchConfiguration("sim")

    # share/amr_mission_control -> ../../../src/amr_mission_control/gui (merged install)
    gui_src_path = os.path.join(pkg, "..", "..", "..", "src", "amr_mission_control", "gui")
    if not os.path.isdir(gui_src_path):
        # Isolated install: resolve relative to the workspace root (launch from there)
        gui_src_path = os.path.abspath("src/amr_mission_control/gui")

    # Empty default → mission_manager uses ~/.ros/amr_locations.json,
    # which survives colcon rebuilds.  The install-copy version of
    # config/locations.json was getting clobbered every build, wiping
    # any GUI-saved lanes/racks.  Pass an explicit path if you want
    # to override.
    return LaunchDescription([
        DeclareLaunchArgument(
            "sim", default_value="false", choices=["true", "false"],
            description="true when running against the Gazebo simulation (sim clock)",
        ),
        DeclareLaunchArgument(
            "locations_file", default_value="",
            description="Path to rack/dock locations JSON "
                        "(empty → ~/.ros/amr_locations.json)",
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
            "open_browser", default_value=sim,
            description="Open a local browser (defaults to the value of sim; the Pi is headless)",
        ),

        # ── Mission Manager ──────────────────────────────────────────
        Node(
            package="amr_mission_control",
            executable="mission_manager",
            name="mission_manager",
            output="screen",
            parameters=[
                os.path.join(pkg, "config", "mission_manager.yaml"),
                {
                    "use_sim_time": sim,
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
                "use_sim_time": sim,
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
        LogInfo(msg="  Mission Control GUI: http://<this-machine-ip>:5173"),
        LogInfo(msg="  Rosbridge WS:        ws://<this-machine-ip>:9090"),
        LogInfo(msg="═══════════════════════════════════════════════════"),
    ])
