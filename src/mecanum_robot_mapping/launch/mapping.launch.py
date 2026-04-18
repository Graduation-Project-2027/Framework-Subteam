import os
import time

import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
    Shutdown,
)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EqualsSubstitution,
    LaunchConfiguration,
    PathJoinSubstitution,
)
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def _as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _run_mapping_preflight(context, *args, **kwargs):
    if not _as_bool(LaunchConfiguration("preflight_check").perform(context)):
        return [
            LogInfo(msg="Mapping preflight disabled; skipping topic readiness checks.")
        ]

    timeout_sec = float(LaunchConfiguration("preflight_timeout_sec").perform(context))
    poll_sec = float(LaunchConfiguration("preflight_poll_sec").perform(context))
    use_sim_time = _as_bool(LaunchConfiguration("use_sim_time").perform(context))
    mode = LaunchConfiguration("mode").perform(context)

    scan_topic = "/scan_front_filtered" if mode == "2l" else "/scan"
    required_topics = [scan_topic, "/odometry/filtered"]
    if use_sim_time:
        required_topics.append("/clock")

    ros_context = rclpy.context.Context()
    rclpy.init(args=None, context=ros_context)
    graph_node = rclpy.create_node("mapping_preflight_check", context=ros_context)
    executor = SingleThreadedExecutor(context=ros_context)
    executor.add_node(graph_node)
    deadline = time.time() + timeout_sec
    last_counts = {topic: 0 for topic in required_topics}
    topic_received = {topic: False for topic in required_topics}

    def _mark_received(topic_name):
        def _callback(_msg):
            topic_received[topic_name] = True

        return _callback

    graph_node.create_subscription(
        LaserScan,
        scan_topic,
        _mark_received(scan_topic),
        qos_profile_sensor_data,
    )
    graph_node.create_subscription(
        Odometry,
        "/odometry/filtered",
        _mark_received("/odometry/filtered"),
        qos_profile_sensor_data,
    )
    if use_sim_time:
        graph_node.create_subscription(
            Clock,
            "/clock",
            _mark_received("/clock"),
            qos_profile_sensor_data,
        )

    try:
        while time.time() < deadline:
            duplicate_sources = []
            missing_topics = []
            missing_messages = []

            executor.spin_once(timeout_sec=poll_sec)

            for topic in required_topics:
                count = len(graph_node.get_publishers_info_by_topic(topic))
                last_counts[topic] = count
                if count == 0:
                    missing_topics.append(topic)
                elif count > 1:
                    duplicate_sources.append(f"{topic}={count}")
                if not topic_received[topic]:
                    missing_messages.append(topic)

            if duplicate_sources:
                message = (
                    "Mapping preflight failed: multiple publishers detected "
                    f"({', '.join(duplicate_sources)}). "
                    "This usually means multiple Gazebo/bridge bringup instances are running."
                )
                return [LogInfo(msg=message), Shutdown(reason=message)]

            if not missing_topics and not missing_messages:
                counts = ", ".join(
                    f"{topic}={last_counts[topic]}" for topic in required_topics
                )
                return [
                    LogInfo(
                        msg=(
                            "Mapping preflight passed: "
                            f"{counts}. Starting slam_toolbox mapping."
                        )
                    )
                ]

        counts = ", ".join(f"{topic}={last_counts[topic]}" for topic in required_topics)
        no_data = ", ".join(
            topic for topic in required_topics if not topic_received[topic]
        )
        message = (
            "Mapping preflight failed: required topics are not ready "
            f"after {timeout_sec:.1f}s ({counts}). "
            f"No messages received yet on: [{no_data}]. "
            "Expected live scan, filtered odometry, and sim clock (if enabled)."
        )
        return [LogInfo(msg=message), Shutdown(reason=message)]
    finally:
        executor.remove_node(graph_node)
        graph_node.destroy_node()
        if ros_context.ok():
            ros_context.shutdown()


def generate_launch_description():
    pkg_mapping = get_package_share_directory("mecanum_robot_mapping")
    pkg_navigation = get_package_share_directory("mecanum_robot_navigation")

    rviz_launch_arg = DeclareLaunchArgument(
        "rviz", default_value="true", description="Open RViz"
    )

    rviz_config_arg = DeclareLaunchArgument(
        "rviz_config", default_value="mapping.rviz", description="RViz config file"
    )

    sim_time_arg = DeclareLaunchArgument(
        "use_sim_time", default_value="True", description="Flag to enable use_sim_time"
    )

    mode_arg = DeclareLaunchArgument(
        "mode", default_value="single", description="Mapping mode: single or 2l"
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

    slam_toolbox_launch_path = os.path.join(
        get_package_share_directory("slam_toolbox"), "launch", "online_async_launch.py"
    )

    slam_toolbox_params_path = os.path.join(
        pkg_mapping,
        "config",
        "slam_toolbox_mapping.yaml",
    )

    slam_toolbox_params_path_2l = os.path.join(
        pkg_mapping,
        "config",
        "slam_toolbox_mapping_2l.yaml",
    )

    laser_filters_params_path = os.path.join(
        pkg_mapping,
        "config",
        "laser_filters_2l.yaml",
    )

    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
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
        condition=IfCondition(EqualsSubstitution(LaunchConfiguration("mode"), "2l")),
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
        condition=IfCondition(EqualsSubstitution(LaunchConfiguration("mode"), "2l")),
    )

    mapping_preflight = OpaqueFunction(function=_run_mapping_preflight)

    slam_toolbox_launch_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_toolbox_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "slam_params_file": slam_toolbox_params_path,
        }.items(),
        condition=IfCondition(
            EqualsSubstitution(LaunchConfiguration("mode"), "single")
        ),
    )

    slam_toolbox_launch_2l = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_toolbox_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "slam_params_file": slam_toolbox_params_path_2l,
        }.items(),
        condition=IfCondition(EqualsSubstitution(LaunchConfiguration("mode"), "2l")),
    )

    launch_description = LaunchDescription()

    launch_description.add_action(rviz_launch_arg)
    launch_description.add_action(rviz_config_arg)
    launch_description.add_action(sim_time_arg)
    launch_description.add_action(mode_arg)
    launch_description.add_action(preflight_check_arg)
    launch_description.add_action(preflight_timeout_arg)
    launch_description.add_action(preflight_poll_arg)
    launch_description.add_action(rviz_node)
    launch_description.add_action(scan_filter_front_node)
    launch_description.add_action(scan_filter_rear_node)
    launch_description.add_action(mapping_preflight)
    launch_description.add_action(slam_toolbox_launch_single)
    launch_description.add_action(slam_toolbox_launch_2l)

    return launch_description
