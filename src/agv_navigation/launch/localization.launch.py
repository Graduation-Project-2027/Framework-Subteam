import os
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    SetLaunchConfiguration,
)
from launch.conditions import IfCondition, LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    pkg_agv_navigation = get_package_share_directory(
        "agv_navigation"
    )

    rviz_launch_arg = DeclareLaunchArgument(
        "rviz", default_value="true", description="Open RViz"
    )

    rviz_config_arg = DeclareLaunchArgument(
        "rviz_config", default_value="localization.rviz", description="RViz config file"
    )

    sim_time_arg = DeclareLaunchArgument(
        "use_sim_time", default_value="True", description="Flag to enable use_sim_time"
    )

    mode_arg = DeclareLaunchArgument(
        "mode", default_value="single", description="Localization mode: single or 2_lidars"
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

    # Path to the Slam Toolbox launch file
    nav2_localization_launch_path = os.path.join(
        get_package_share_directory("nav2_bringup"), "launch", "localization_launch.py"
    )

    localization_params_path = os.path.join(
        get_package_share_directory("agv_navigation"),
        "config",
        "amcl_localization.yaml",
    )

    localization_params_path_2_lidars = os.path.join(
        get_package_share_directory("agv_navigation"),
        "config",
        "amcl_localization_2_lidars.yaml",
    )

    laser_filters_params_path = os.path.join(
        get_package_share_directory("agv_navigation"),
        "config",
        "laser_filters_2_lidars.yaml",
    )

    map_file_path = os.path.join(
        get_package_share_directory("agv_navigation"), "maps", "map.yaml"
    )

    # Accept world args for consistent launcher UX.
    # localization.launch.py itself doesn't spawn Gazebo/world.
    world_package_passthrough = SetLaunchConfiguration(
        "world_package", LaunchConfiguration("world_package")
    )
    world_passthrough = SetLaunchConfiguration("world", LaunchConfiguration("world"))

    # Launch rviz
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        arguments=[
            "-d",
            PathJoinSubstitution(
                [
                    pkg_agv_navigation,
                    "rviz",
                    LaunchConfiguration("rviz_config"),
                ]
            ),
        ],
        condition=IfCondition(LaunchConfiguration("rviz")),
        parameters=[
            {"use_sim_time": LaunchConfiguration("use_sim_time")},
        ],
    )

    scan_filter_front_node = Node(
        package="laser_filters",
        executable="scan_to_scan_filter_chain",
        name="scan_filter_front",
        output="screen",
        parameters=[
            laser_filters_params_path,
            {"use_sim_time": LaunchConfiguration("use_sim_time")},
        ],
        remappings=[("scan", "/scan"), ("scan_filtered", "/scan_front_filtered")],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    scan_filter_rear_node = Node(
        package="laser_filters",
        executable="scan_to_scan_filter_chain",
        name="scan_filter_rear",
        output="screen",
        parameters=[
            laser_filters_params_path,
            {"use_sim_time": LaunchConfiguration("use_sim_time")},
        ],
        remappings=[("scan", "/scan_rear"), ("scan_filtered", "/scan_rear_filtered")],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    scan_merger_node = Node(
        package="agv_mapping",
        executable="scan_merger.py",
        name="scan_merger",
        output="screen",
        parameters=[
            {"use_sim_time": LaunchConfiguration("use_sim_time")},
        ],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    localization_launch_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_localization_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "params_file": localization_params_path,
            "map": map_file_path,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    localization_launch_2_lidars = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_localization_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "params_file": localization_params_path_2_lidars,
            "map": map_file_path,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    launchDescriptionObject = LaunchDescription()

    launchDescriptionObject.add_action(rviz_launch_arg)
    launchDescriptionObject.add_action(rviz_config_arg)
    launchDescriptionObject.add_action(sim_time_arg)
    launchDescriptionObject.add_action(mode_arg)
    launchDescriptionObject.add_action(world_package_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(world_package_passthrough)
    launchDescriptionObject.add_action(world_passthrough)
    launchDescriptionObject.add_action(rviz_node)
    launchDescriptionObject.add_action(scan_filter_front_node)
    launchDescriptionObject.add_action(scan_filter_rear_node)
    launchDescriptionObject.add_action(scan_merger_node)
    launchDescriptionObject.add_action(localization_launch_single)
    launchDescriptionObject.add_action(localization_launch_2_lidars)

    return launchDescriptionObject
