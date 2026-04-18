import os
import time
import rclpy
from nav_msgs.msg import Odometry
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import LaserScan
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
    SetLaunchConfiguration,
    Shutdown,
)
from launch.conditions import IfCondition, LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from ament_index_python.packages import (
    PackageNotFoundError,
    get_package_share_directory,
)


def _as_bool(value):
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _run_navigation_preflight(context, *args, **kwargs):
    if not _as_bool(LaunchConfiguration("preflight_check").perform(context)):
        return [
            LogInfo(
                msg="Navigation preflight disabled; skipping /scan and /clock readiness checks."
            )
        ]

    timeout_sec = float(LaunchConfiguration("preflight_timeout_sec").perform(context))
    poll_sec = float(LaunchConfiguration("preflight_poll_sec").perform(context))
    use_sim_time = _as_bool(LaunchConfiguration("use_sim_time").perform(context))

    required_topics = ["/scan", "/odometry/filtered"]
    if use_sim_time:
        required_topics.append("/clock")

    initialized_here = False
    if not rclpy.ok():
        rclpy.init(args=None)
        initialized_here = True

    graph_node = rclpy.create_node("navigation_with_slam_preflight_check")
    deadline = time.time() + timeout_sec
    last_counts = {topic: 0 for topic in required_topics}
    topic_received = {topic: False for topic in required_topics}

    def _mark_received(topic_name):
        def _callback(_msg):
            topic_received[topic_name] = True

        return _callback

    graph_node.create_subscription(LaserScan, "/scan", _mark_received("/scan"), 10)
    graph_node.create_subscription(
        Odometry,
        "/odometry/filtered",
        _mark_received("/odometry/filtered"),
        10,
    )
    if use_sim_time:
        graph_node.create_subscription(Clock, "/clock", _mark_received("/clock"), 10)

    try:
        while time.time() < deadline:
            duplicate_sources = []
            missing_topics = []
            missing_messages = []

            rclpy.spin_once(graph_node, timeout_sec=poll_sec)

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
                    "Navigation preflight failed: multiple publishers detected "
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
                            "Navigation preflight passed: "
                            f"{counts}. Starting SLAM and navigation."
                        )
                    )
                ]

        counts = ", ".join(f"{topic}={last_counts[topic]}" for topic in required_topics)
        no_data = ", ".join(
            topic for topic in required_topics if not topic_received[topic]
        )
        message = (
            "Navigation preflight failed: required topics are not ready "
            f"after {timeout_sec:.1f}s ({counts}). "
            f"No messages received yet on: [{no_data}]. "
            "Expected live /scan, /odometry/filtered and /clock (if sim time) before map->odom can stabilize."
        )
        return [LogInfo(msg=message), Shutdown(reason=message)]
    finally:
        graph_node.destroy_node()
        if initialized_here and rclpy.ok():
            rclpy.shutdown()


def generate_launch_description():

    pkg_mecanum_robot_navigation = get_package_share_directory(
        "mecanum_robot_navigation"
    )

    gazebo_models_path, ignore_last_dir = os.path.split(pkg_mecanum_robot_navigation)
    existing_resource_path = os.environ.get("GZ_SIM_RESOURCE_PATH", "")
    if existing_resource_path:
        os.environ["GZ_SIM_RESOURCE_PATH"] = (
            existing_resource_path + os.pathsep + gazebo_models_path
        )
    else:
        os.environ["GZ_SIM_RESOURCE_PATH"] = gazebo_models_path

    rviz_launch_arg = DeclareLaunchArgument(
        "rviz", default_value="true", description="Open RViz"
    )

    rviz_config_arg = DeclareLaunchArgument(
        "rviz_config", default_value="navigation.rviz", description="RViz config file"
    )

    sim_time_arg = DeclareLaunchArgument(
        "use_sim_time", default_value="True", description="Flag to enable use_sim_time"
    )

    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="single",
        description="Navigation mode: single or 2l",
    )

    world_package_arg = DeclareLaunchArgument(
        "world_package",
        default_value="mecanum_robot_description",
        description="Package that contains the Gazebo world file",
    )

    world_arg = DeclareLaunchArgument(
        "world",
        default_value="worlds/warehouse_v2.sdf",
        description="World file path relative to world_package share directory",
    )

    use_twist_marker_arg = DeclareLaunchArgument(
        "use_twist_marker",
        default_value="false",
        description="Launch interactive marker twist server if available",
    )

    preflight_check_arg = DeclareLaunchArgument(
        "preflight_check",
        default_value="true",
        description="Check /scan and /clock publisher readiness before launching Nav2",
    )

    preflight_timeout_arg = DeclareLaunchArgument(
        "preflight_timeout_sec",
        default_value="60.0",
        description="Maximum seconds to wait for preflight topics",
    )

    preflight_poll_arg = DeclareLaunchArgument(
        "preflight_poll_sec",
        default_value="0.25",
        description="Polling period in seconds for preflight checks",
    )

    # Generate path to config file
    interactive_marker_config_file_path = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "interactive_marker_planar.yaml",
    )

    nav2_navigation_launch_path = os.path.join(
        get_package_share_directory("nav2_bringup"), "launch", "navigation_launch.py"
    )

    navigation_params_path = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "navigation.yaml",
    )

    navigation_params_path_2l = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "navigation_2l.yaml",
    )

    slam_toolbox_params_path = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "slam_toolbox_mapping.yaml",
    )

    slam_toolbox_params_path_2l = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "slam_toolbox_mapping_2l.yaml",
    )

    laser_filters_params_path = os.path.join(
        get_package_share_directory("mecanum_robot_navigation"),
        "config",
        "laser_filters_2l.yaml",
    )

    # Accept world args for consistent launcher UX.
    # navigation_with_slam.launch.py itself doesn't spawn Gazebo/world.
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
                    pkg_mecanum_robot_navigation,
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

    try:
        get_package_share_directory("interactive_marker_twist_server")
        interactive_marker_twist_server_node = Node(
            package="interactive_marker_twist_server",
            executable="marker_server",
            name="twist_server_node",
            parameters=[interactive_marker_config_file_path],
            output="screen",
            condition=IfCondition(LaunchConfiguration("use_twist_marker")),
        )
    except PackageNotFoundError:
        interactive_marker_twist_server_node = LogInfo(
            msg="interactive_marker_twist_server not found; skipping twist marker server."
        )

    # Path to the Slam Toolbox launch file
    slam_toolbox_launch_path = os.path.join(
        get_package_share_directory("slam_toolbox"), "launch", "online_async_launch.py"
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
        condition=LaunchConfigurationEquals("mode", "2l"),
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
        condition=LaunchConfigurationEquals("mode", "2l"),
    )

    slam_toolbox_launch_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_toolbox_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "slam_params_file": slam_toolbox_params_path,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    slam_toolbox_launch_2l = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(slam_toolbox_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "slam_params_file": slam_toolbox_params_path_2l,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "2l"),
    )

    navigation_launch_single = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_navigation_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "params_file": navigation_params_path,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    navigation_launch_2l = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_navigation_launch_path),
        launch_arguments={
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "params_file": navigation_params_path_2l,
        }.items(),
        condition=LaunchConfigurationEquals("mode", "2l"),
    )

    navigation_preflight = OpaqueFunction(function=_run_navigation_preflight)

    launchDescriptionObject = LaunchDescription()

    launchDescriptionObject.add_action(rviz_launch_arg)
    launchDescriptionObject.add_action(rviz_config_arg)
    launchDescriptionObject.add_action(sim_time_arg)
    launchDescriptionObject.add_action(mode_arg)
    launchDescriptionObject.add_action(world_package_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(world_package_passthrough)
    launchDescriptionObject.add_action(world_passthrough)
    launchDescriptionObject.add_action(use_twist_marker_arg)
    launchDescriptionObject.add_action(preflight_check_arg)
    launchDescriptionObject.add_action(preflight_timeout_arg)
    launchDescriptionObject.add_action(preflight_poll_arg)
    launchDescriptionObject.add_action(rviz_node)
    launchDescriptionObject.add_action(interactive_marker_twist_server_node)
    launchDescriptionObject.add_action(scan_filter_front_node)
    launchDescriptionObject.add_action(scan_filter_rear_node)
    launchDescriptionObject.add_action(navigation_preflight)
    launchDescriptionObject.add_action(slam_toolbox_launch_single)
    launchDescriptionObject.add_action(slam_toolbox_launch_2l)
    launchDescriptionObject.add_action(navigation_launch_single)
    launchDescriptionObject.add_action(navigation_launch_2l)

    return launchDescriptionObject
