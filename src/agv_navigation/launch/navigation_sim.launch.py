import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg_navigation = get_package_share_directory("agv_navigation")

    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="single",
        description="Robot and navigation mode: single or 2_lidars",
    )

    rviz_arg = DeclareLaunchArgument(
        "rviz",
        default_value="true",
        description="Open RViz",
    )

    rviz_config_arg = DeclareLaunchArgument(
        "rviz_config",
        default_value="navigation.rviz",
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

    map_file_arg = DeclareLaunchArgument(
        "map_file",
        default_value=os.path.join(pkg_navigation, "maps", "Hang1"),
        description="Path to SLAM Toolbox serialized map (without extension). Empty = start fresh.",
    )

    nav_start_delay_arg = DeclareLaunchArgument(
        "nav_start_delay_sec",
        default_value="10.0",
        description="Delay before starting navigation launch after simulation bringup",
    )

    # 1. Spawn robot + Gazebo
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

    # 2. RViz — launched directly (not inside TimerAction) so it starts immediately
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
        arguments=[
            "-d",
            PathJoinSubstitution(
                [pkg_navigation, "rviz", LaunchConfiguration("rviz_config")]
            ),
        ],
        parameters=[{"use_sim_time": LaunchConfiguration("use_sim_time")}],
    )

    # 3. Navigation (SLAM + Nav2 + filters + merger) — delayed to let Gazebo start
    navigation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_navigation, "launch", "navigation.launch.py")
        ),
        launch_arguments={
            "mode": LaunchConfiguration("mode"),
            "rviz": "false",
            "rviz_config": LaunchConfiguration("rviz_config"),
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "preflight_check": "false",
            "map_file": LaunchConfiguration("map_file"),
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
    launch_description.add_action(map_file_arg)
    launch_description.add_action(nav_start_delay_arg)
    launch_description.add_action(spawn_launch)
    launch_description.add_action(rviz_node)
    launch_description.add_action(
        TimerAction(
            period=LaunchConfiguration("nav_start_delay_sec"),
            actions=[navigation_launch],
        )
    )

    return launch_description
