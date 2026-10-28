"""SLAM mapping (slam_toolbox in mapping mode) on top of the robot bringup.

    ros2 launch amr_mapping mapping.launch.py              # real robot
    ros2 launch amr_mapping mapping.launch.py sim:=true    # Gazebo + RViz

Drive the robot around (teleop TUI or GUI), then save both map formats; see docs/launch.md.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    pkg_mapping = FindPackageShare("amr_mapping")
    sim = LaunchConfiguration("sim")

    args = [
        DeclareLaunchArgument(
            "sim", default_value="false", choices=["true", "false"],
            description="true: Gazebo simulation + RViz instead of the real robot"),
        DeclareLaunchArgument(
            "rviz", default_value=sim,
            description="Open RViz (defaults to the value of sim)"),
        DeclareLaunchArgument(
            "world", default_value="worlds/warehouse_v2.sdf",
            description="Gazebo world, relative to amr_description (sim only)"),
        DeclareLaunchArgument(
            "start_delay", default_value=PythonExpression(["'10.0' if '", sim, "' == 'true' else '0.0'"]),
            description="Seconds to wait for the base (Gazebo) before starting SLAM"),
    ]

    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("amr_bringup"), "launch", "bringup.launch.py"])),
        launch_arguments={"sim": sim, "rviz": "false", "world": LaunchConfiguration("world")}.items(),
    )

    scan_pipeline = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([pkg_mapping, "launch", "scan_pipeline.launch.py"])),
        launch_arguments={"use_sim_time": sim}.items(),
    )

    slam = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("slam_toolbox"), "launch", "online_async_launch.py"])),
        launch_arguments={
            "use_sim_time": sim,
            "slam_params_file": PathJoinSubstitution([pkg_mapping, "config", "slam_toolbox_mapping.yaml"]),
        }.items(),
    )

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        output="screen",
        arguments=["-d", PathJoinSubstitution([pkg_mapping, "rviz", "mapping.rviz"])],
        parameters=[{"use_sim_time": sim}],
        condition=IfCondition(LaunchConfiguration("rviz")),
    )

    return LaunchDescription(args + [
        bringup,
        rviz,
        TimerAction(period=LaunchConfiguration("start_delay"), actions=[scan_pipeline, slam]),
    ])
