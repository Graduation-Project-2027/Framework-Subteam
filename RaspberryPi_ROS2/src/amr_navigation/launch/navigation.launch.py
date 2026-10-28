"""Autonomous navigation: SLAM Toolbox localization on a saved map + Nav2 (MPPI).

    ros2 launch amr_navigation navigation.launch.py              # real robot
    ros2 launch amr_navigation navigation.launch.py sim:=true    # Gazebo + RViz

Includes the robot bringup and the dual-lidar scan pipeline, so this is the
only launch needed (besides mission control) to run the AMR.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
    RegisterEventHandler,
    TimerAction,
)
from launch.conditions import IfCondition
from launch.events import matches_action
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, PythonExpression
from launch_ros.actions import LifecycleNode, Node
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState
from launch_ros.substitutions import FindPackageShare
from lifecycle_msgs.msg import Transition
from nav2_common.launch import RewrittenYaml


def _nav2_with_map(context, pkg_nav):
    """Nav2, plus a map_server for the saved map image when there is one.

    Localization stays on the slam_toolbox pose graph (/map); the global
    costmap's static layer reads /map_static instead, so walls and no-go areas
    painted in the GUI map editor are respected by the planner.
    """
    use_sim_time = LaunchConfiguration("sim").perform(context) == "true"
    map_yaml = LaunchConfiguration("map_file").perform(context) + ".yaml"
    params = os.path.join(pkg_nav, "config", "navigation.yaml")
    actions = []
    if LaunchConfiguration("use_edited_map").perform(context) == "true" and os.path.exists(map_yaml):
        params = RewrittenYaml(
            source_file=params, param_rewrites={"map_topic": "/map_static"}, convert_types=True,
        ).perform(context)
        actions += [
            Node(
                package="nav2_map_server",
                executable="map_server",
                name="map_server_static",
                output="screen",
                parameters=[{
                    "use_sim_time": use_sim_time,
                    "yaml_filename": map_yaml,
                    "topic_name": "map_static",
                    "frame_id": "map",
                }],
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_map_static",
                output="screen",
                parameters=[{
                    "use_sim_time": use_sim_time,
                    "autostart": True,
                    "node_names": ["map_server_static"],
                }],
            ),
            LogInfo(msg=f"[Map] Global planning on the saved image {map_yaml} (/map_static)."),
        ]
    else:
        actions.append(LogInfo(msg="[Map] Global planning on the live SLAM map (/map)."))

    actions.append(IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("nav2_bringup"), "launch", "navigation_launch.py"])),
        launch_arguments={"use_sim_time": "true" if use_sim_time else "false", "params_file": params}.items(),
    ))
    return actions


def generate_launch_description():
    pkg_nav = get_package_share_directory("amr_navigation")
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
            "map_file", default_value=os.path.join(pkg_nav, "maps", "Hang1"),
            description="slam_toolbox serialized map (.posegraph/.data) without extension"),
        DeclareLaunchArgument(
            "start_delay", default_value=PythonExpression(["'10.0' if '", sim, "' == 'true' else '0.0'"]),
            description="Seconds to wait for the base (Gazebo) before starting SLAM and Nav2"),
        DeclareLaunchArgument(
            "use_edited_map", default_value="true", choices=["true", "false"],
            description="Plan on the saved map image (<map_file>.yaml, editable in the GUI) "
                        "instead of the live SLAM map"),
    ]

    bringup = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("amr_bringup"), "launch", "bringup.launch.py"])),
        launch_arguments={"sim": sim, "rviz": "false", "world": LaunchConfiguration("world")}.items(),
    )

    scan_pipeline = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            PathJoinSubstitution([FindPackageShare("amr_mapping"), "launch", "scan_pipeline.launch.py"])),
        launch_arguments={"use_sim_time": sim}.items(),
    )

    # slam_toolbox runs without a lifecycle manager, so configure/activate it here.
    slam = LifecycleNode(
        package="slam_toolbox",
        executable="async_slam_toolbox_node",
        name="slam_toolbox",
        namespace="",
        output="screen",
        parameters=[
            os.path.join(pkg_nav, "config", "slam_toolbox_localization.yaml"),
            {
                "use_sim_time": sim,
                "use_lifecycle_manager": False,
                "map_file_name": LaunchConfiguration("map_file"),
                "map_start_at_dock": True,
            },
        ],
    )
    slam_activate = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=slam,
            start_state="configuring",
            goal_state="inactive",
            entities=[
                LogInfo(msg="[Lifecycle] SLAM Toolbox activating."),
                EmitEvent(event=ChangeState(
                    lifecycle_node_matcher=matches_action(slam),
                    transition_id=Transition.TRANSITION_ACTIVATE,
                )),
            ],
        )
    )
    slam_configure = EmitEvent(event=ChangeState(
        lifecycle_node_matcher=matches_action(slam),
        transition_id=Transition.TRANSITION_CONFIGURE,
    ))

    nav2_and_map = OpaqueFunction(function=_nav2_with_map, args=[pkg_nav])

    rviz = Node(
        package="rviz2",
        executable="rviz2",
        output="screen",
        arguments=["-d", os.path.join(pkg_nav, "rviz", "navigation.rviz")],
        parameters=[{"use_sim_time": sim}],
        condition=IfCondition(LaunchConfiguration("rviz")),
    )

    return LaunchDescription(args + [
        bringup,
        rviz,
        TimerAction(
            period=LaunchConfiguration("start_delay"),
            actions=[scan_pipeline, slam_activate, slam, slam_configure, nav2_and_map],
        ),
    ])
