"""Robot base bringup, the layer every other launch file builds on.

sim:=false (default) runs on the real AMR (Raspberry Pi 5): robot_state_publisher,
the EKF and the hardware drivers from amr_hardware (lidars, IMU, CAN bridge).
sim:=true replaces the drivers with Gazebo (world + spawned robot + ros_gz bridge)
and opens RViz.

    ros2 launch amr_bringup bringup.launch.py              # real robot
    ros2 launch amr_bringup bringup.launch.py sim:=true    # Gazebo + RViz
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import AppendEnvironmentVariable, DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    pkg_bringup = get_package_share_directory("amr_bringup")
    pkg_description = get_package_share_directory("amr_description")

    sim = LaunchConfiguration("sim")
    rviz = LaunchConfiguration("rviz")
    world = LaunchConfiguration("world")

    args = [
        DeclareLaunchArgument(
            "sim", default_value="false", choices=["true", "false"],
            description="true: Gazebo simulation + RViz instead of the real robot"),
        DeclareLaunchArgument(
            "rviz", default_value=sim,
            description="Open RViz (defaults to the value of sim)"),
        DeclareLaunchArgument(
            "world", default_value="worlds/warehouse_v2.sdf",
            description="Gazebo world, relative to the amr_description share directory (sim only)"),
    ]

    robot_description = ParameterValue(
        Command([
            "xacro '", os.path.join(pkg_description, "urdf", "urdf", "robot.urdf.xacro"),
            "' use_gazebo:=", sim,
        ]),
        value_type=str,
    )

    robot_state_publisher = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        output="screen",
        parameters=[{"robot_description": robot_description, "use_sim_time": sim}],
    )

    ekf = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_filter_node",
        output="screen",
        parameters=[os.path.join(pkg_bringup, "config", "ekf.yaml"), {"use_sim_time": sim}],
    )

    # ── Real robot ───────────────────────────────────────────────────
    hardware = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("amr_hardware"), "launch", "hardware.launch.py"])),
        condition=UnlessCondition(sim),
    )

    # ── Simulation ───────────────────────────────────────────────────
    # Gazebo resolves the world's model:// URIs and the robot's package:// meshes here.
    gz_resource_path = AppendEnvironmentVariable(
        "GZ_SIM_RESOURCE_PATH",
        os.pathsep.join([os.path.join(pkg_description, "models"), os.path.dirname(pkg_description)]),
    )

    gazebo = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("ros_gz_sim"), "launch", "gz_sim.launch.py"])),
        launch_arguments={
            "gz_args": [PathJoinSubstitution([pkg_description, world]), " -r"],
            "on_exit_shutdown": "true",
        }.items(),
        condition=IfCondition(sim),
    )

    # Spawn pose matches map_start_pose of the Hang1 map (amr_navigation slam config).
    spawn = Node(
        package="ros_gz_sim",
        executable="create",
        output="screen",
        arguments=[
            "-name", "amr", "-topic", "robot_description",
            "-x", "-1.54532", "-y", "1.62148", "-z", "0.01", "-Y", "0.0144602",
        ],
        parameters=[{"use_sim_time": True}],
        condition=IfCondition(sim),
    )

    gz_bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        output="screen",
        parameters=[{
            "config_file": os.path.join(pkg_bringup, "config", "gz_bridge.yaml"),
            "use_sim_time": True,
        }],
        condition=IfCondition(sim),
    )

    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        output="screen",
        arguments=["-d", os.path.join(pkg_bringup, "rviz", "bringup.rviz")],
        parameters=[{"use_sim_time": sim}],
        condition=IfCondition(rviz),
    )

    return LaunchDescription(args + [
        robot_state_publisher,
        ekf,
        hardware,
        gz_resource_path,
        gazebo,
        spawn,
        gz_bridge,
        rviz_node,
    ])
