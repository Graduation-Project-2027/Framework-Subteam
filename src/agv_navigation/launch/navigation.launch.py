import os

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    IncludeLaunchDescription,
    LogInfo,
    RegisterEventHandler,
)
from launch.conditions import IfCondition, LaunchConfigurationEquals
from launch.events import matches_action
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import LifecycleNode, Node
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from lifecycle_msgs.msg import Transition
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    pkg_nav = get_package_share_directory("agv_navigation")

    # ── Paths ────────────────────────────────────────────────────────────
    nav2_launch = os.path.join(
        get_package_share_directory("nav2_bringup"), "launch", "navigation_launch.py"
    )
    slam_launch = os.path.join(
        get_package_share_directory("slam_toolbox"), "launch", "online_async_launch.py"
    )

    nav_params_single = os.path.join(pkg_nav, "config", "navigation.yaml")
    nav_params_2_lidars = os.path.join(pkg_nav, "config", "navigation_2_lidars.yaml")
    slam_params_single = os.path.join(pkg_nav, "config", "slam_toolbox_localization_single.yaml")
    slam_params_2_lidars = os.path.join(
        pkg_nav, "config", "slam_toolbox_localization_2_lidars.yaml"
    )
    laser_filter_params = os.path.join(pkg_nav, "config", "laser_filters_2_lidars.yaml")
    default_map = os.path.join(pkg_nav, "maps", "Hang1")

    # ── Arguments ────────────────────────────────────────────────────────
    args = [
        DeclareLaunchArgument("mode", default_value="single",
                              description="single or 2_lidars"),
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        DeclareLaunchArgument("rviz", default_value="true", description="Open RViz"),
        DeclareLaunchArgument("rviz_config", default_value="navigation.rviz"),
        DeclareLaunchArgument("map_file", default_value=default_map,
                              description="SLAM Toolbox serialized map (no extension)"),
    ]

    use_sim_time = LaunchConfiguration("use_sim_time")
    mode = LaunchConfiguration("mode")

    # ── RViz ─────────────────────────────────────────────────────────────
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        arguments=[
            "-d",
            PathJoinSubstitution([pkg_nav, "rviz", LaunchConfiguration("rviz_config")]),
        ],
        parameters=[{"use_sim_time": use_sim_time}],
        condition=IfCondition(LaunchConfiguration("rviz")),
    )

    # ── Scan filters + merger (2_lidars only) ────────────────────────────
    scan_filter_front = Node(
        package="laser_filters",
        executable="scan_to_scan_filter_chain",
        name="scan_filter_front",
        output="screen",
        parameters=[laser_filter_params, {"use_sim_time": use_sim_time}],
        remappings=[("scan", "/scan"), ("scan_filtered", "/scan_front_filtered")],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    scan_filter_rear = Node(
        package="laser_filters",
        executable="scan_to_scan_filter_chain",
        name="scan_filter_rear",
        output="screen",
        parameters=[laser_filter_params, {"use_sim_time": use_sim_time}],
        remappings=[("scan", "/scan_rear"), ("scan_filtered", "/scan_rear_filtered")],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    scan_merger = Node(
        package="agv_mapping",
        executable="scan_merger.py",
        name="scan_merger",
        output="screen",
        # Merge the PAYLOAD-FILTERED scans so SLAM's matcher never sees the
        # carried rack legs (otherwise they get baked into the map as ghost
        # static obstacles that later fail navigation).
        parameters=[{
            "use_sim_time": use_sim_time,
            "front_topic": "/scan_front_nav",
            "rear_topic": "/scan_rear_nav",
        }],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # Carried-payload self-filter: removes the lifted rack's legs from the
    # scans the costmap AND slam consume, so the robot doesn't cage itself.
    # Pure pass-through (filtered → _nav) until carrying.
    payload_filter = Node(
        package="agv_mapping",
        executable="payload_filter.py",
        name="payload_filter",
        output="screen",
        parameters=[{"use_sim_time": use_sim_time}],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # ── SLAM Toolbox ─────────────────────────────────────────────────────

    # Single-lidar: use the standard slam_toolbox launch (handles lifecycle)
    slam_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_launch),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "slam_params_file": slam_params_single,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    # 2-lidars: LifecycleNode with localization config + map loading
    slam_2_lidars = LifecycleNode(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        namespace="",
        output="screen",
        parameters=[
            slam_params_2_lidars,
            {
                "use_sim_time": use_sim_time,
                "use_lifecycle_manager": False,
                "map_file_name": LaunchConfiguration("map_file"),
                "map_start_at_dock": True,
            },
        ],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # Lifecycle transitions: configure → activate (same as online_async_launch.py)
    slam_configure = EmitEvent(
        event=ChangeState(
            lifecycle_node_matcher=matches_action(slam_2_lidars),
            transition_id=Transition.TRANSITION_CONFIGURE,
        ),
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    slam_activate = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=slam_2_lidars,
            start_state="configuring",
            goal_state="inactive",
            entities=[
                LogInfo(msg="[Lifecycle] SLAM Toolbox activating."),
                EmitEvent(
                    event=ChangeState(
                        lifecycle_node_matcher=matches_action(slam_2_lidars),
                        transition_id=Transition.TRANSITION_ACTIVATE,
                    )
                ),
            ],
        ),
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # ── Nav2 ─────────────────────────────────────────────────────────────
    nav2_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_launch),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "params_file": nav_params_single,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    nav2_2_lidars = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_launch),
        launch_arguments={
            "use_sim_time": use_sim_time,
            "params_file": nav_params_2_lidars,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # ── Build launch description ─────────────────────────────────────────
    ld = LaunchDescription()

    for arg in args:
        ld.add_action(arg)

    ld.add_action(rviz_node)

    # Scan processing (2_lidars)
    ld.add_action(scan_filter_front)
    ld.add_action(scan_filter_rear)
    ld.add_action(scan_merger)
    ld.add_action(payload_filter)

    # SLAM
    ld.add_action(slam_single)
    ld.add_action(slam_2_lidars)
    ld.add_action(slam_configure)
    ld.add_action(slam_activate)

    # Nav2
    ld.add_action(nav2_single)
    ld.add_action(nav2_2_lidars)

    return ld
