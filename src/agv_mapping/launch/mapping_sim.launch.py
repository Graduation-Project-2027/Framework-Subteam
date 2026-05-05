import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_mapping = get_package_share_directory("agv_mapping")
    pkg_navigation = get_package_share_directory("agv_navigation")

    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="single",
        description="Robot and mapping mode: single or 2_lidars",
    )

    rviz_arg = DeclareLaunchArgument(
        "rviz",
        default_value="true",
        description="Open RViz in mapping launch",
    )

    rviz_config_arg = DeclareLaunchArgument(
        "rviz_config",
        default_value="mapping.rviz",
        description="RViz config file",
    )

    use_sim_time_arg = DeclareLaunchArgument(
        "use_sim_time",
        default_value="True",
        description="Flag to enable use_sim_time",
    )

    world_package_arg = DeclareLaunchArgument(
        "world_package",
        default_value="agv_description",
        description="Package that contains the Gazebo world file",
    )

    world_arg = DeclareLaunchArgument(
        "world",
        default_value="worlds/warehouse_v2.sdf",
        description="World file path relative to world_package share directory",
    )

    urdf_arg = DeclareLaunchArgument(
        "urdf",
        default_value="",
        description="Optional explicit URDF file override",
    )

    preflight_check_arg = DeclareLaunchArgument(
        "preflight_check",
        default_value="true",
        description="Check required mapping topics before launching slam_toolbox",
    )

    preflight_timeout_arg = DeclareLaunchArgument(
        "preflight_timeout_sec",
        default_value="45.0",
        description="Maximum seconds to wait for preflight topics",
    )

    preflight_poll_arg = DeclareLaunchArgument(
        "preflight_poll_sec",
        default_value="0.25",
        description="Polling period in seconds for preflight checks",
    )

    mapping_start_delay_arg = DeclareLaunchArgument(
        "mapping_start_delay_sec",
        default_value="5.0",
        description="Delay before starting mapping launch after simulation bringup",
    )

    spawn_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_navigation, "launch", "spawn_robot.launch.py")
        ),
        launch_arguments={
            "mode": LaunchConfiguration("mode"),
            "rviz": "false",
            "world_package": LaunchConfiguration("world_package"),
            "world": LaunchConfiguration("world"),
            "urdf": LaunchConfiguration("urdf"),
        }.items(),
    )

    rviz_node = Node(package="rviz2", executable="rviz2", name="rviz2", output="screen",
        arguments=[
            "-d",
            PathJoinSubstitution(
                [
                    pkg_navigation,
                    "rviz",
                    LaunchConfiguration("rviz_config"),
                ]
            ),
        ],
        parameters=[
            {"use_sim_time": LaunchConfiguration("use_sim_time")},
        ],
    )

    mapping_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_mapping, "launch", "mapping.launch.py")
        ),
        launch_arguments={
            "mode": LaunchConfiguration("mode"),
            "rviz": "false",
            "rviz_config": LaunchConfiguration("rviz_config"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "preflight_check": "false",
            "preflight_timeout_sec": LaunchConfiguration("preflight_timeout_sec"),
            "preflight_poll_sec": LaunchConfiguration("preflight_poll_sec"),
        }.items(),
    )

    launch_description = LaunchDescription()
    launch_description.add_action(mode_arg)
    launch_description.add_action(rviz_arg)
    launch_description.add_action(rviz_config_arg)
    launch_description.add_action(use_sim_time_arg)
    launch_description.add_action(world_package_arg)
    launch_description.add_action(world_arg)
    launch_description.add_action(urdf_arg)
    launch_description.add_action(preflight_check_arg)
    launch_description.add_action(preflight_timeout_arg)
    launch_description.add_action(preflight_poll_arg)
    launch_description.add_action(spawn_launch)
    launch_description.add_action(rviz_node)
    launch_description.add_action(mapping_start_delay_arg)
    launch_description.add_action(
        TimerAction(
            period=LaunchConfiguration("mapping_start_delay_sec"),
            actions=[mapping_launch],
        )
    )

    return launch_description
