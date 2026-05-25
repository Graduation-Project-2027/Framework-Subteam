import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition, LaunchConfigurationEquals
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    LaunchConfiguration,
    PathJoinSubstitution,
    Command,
    PythonExpression,
)
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    pkg_urdf_path = get_package_share_directory("agv_description")
    pkg_gazebo_path = get_package_share_directory("agv_bringup")
    pkg_agv_navigation = get_package_share_directory(
        "agv_navigation"
    )
    description_parent = os.path.dirname(pkg_urdf_path)
    existing_resource_path = os.environ.get("GZ_SIM_RESOURCE_PATH", "")
    if existing_resource_path:
        os.environ["GZ_SIM_RESOURCE_PATH"] = (
            description_parent + os.pathsep + existing_resource_path
        )
    else:
        os.environ["GZ_SIM_RESOURCE_PATH"] = description_parent

    gz_bridge_params_path_single = os.path.join(
        get_package_share_directory("agv_navigation"),
        "config",
        "gz_bridge.yaml",
    )
    gz_bridge_params_path_2_lidars = os.path.join(
        get_package_share_directory("agv_navigation"),
        "config",
        "gz_bridge_2_lidars.yaml",
    )

    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="single",
        description="Robot mode: single or 2_lidars",
    )

    rviz_launch_arg = DeclareLaunchArgument(
        "rviz", default_value="true", description="Open RViz."
    )

    world_arg = DeclareLaunchArgument(
        "world",
        default_value="worlds/warehouse_v2.sdf",
        description="World file path relative to world_package share directory",
    )

    world_package_arg = DeclareLaunchArgument(
        "world_package",
        default_value="agv_description",
        description="Package that contains the Gazebo world file",
    )

    model_arg = DeclareLaunchArgument(
        "urdf",
        default_value="",
        description="Optional explicit URDF file override",
    )

    urdf_file_single = PathJoinSubstitution(
        [pkg_urdf_path, "urdf", "urdf", "robot.urdf.xacro"]
    )
    urdf_file_2_lidars = PathJoinSubstitution(
        [pkg_urdf_path, "urdf", "urdf", "2_lidars_robot.urdf.xacro"]
    )
    urdf_file_override = PathJoinSubstitution(
        [pkg_urdf_path, "urdf", "urdf", LaunchConfiguration("urdf")]
    )

    # Launch Gazebo with the specified world
    world_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_gazebo_path, "launch", "world.launch.py"),
        ),
        launch_arguments={
            "world_package": LaunchConfiguration("world_package"),
            "world": LaunchConfiguration("world"),
            "models_package": "agv_description",
        }.items(),
    )

    # Launch rviz
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        arguments=["-d", os.path.join(pkg_urdf_path, "rviz", "rviz.rviz")],
        condition=IfCondition(LaunchConfiguration("rviz")),
        parameters=[
            {"use_sim_time": True},
        ],
    )

    # Spawn the URDF model in Gazebo using the ros_gz_sim create executable
    spawn_urdf_node = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=[
            "-name",
            "my_robot",
            "-topic",
            "robot_description",
            "-x",
            "-1.54532",
            "-y",
            "1.62148",
            "-z",
            "0.01",
            "-Y",
            "0.0144602",
        ],
        output="screen",
        parameters=[
            {"use_sim_time": True},
        ],
    )

    # Node to publish the robot state (TFs) based on the URDF description
    robot_state_publisher_node_single = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="screen",
        parameters=[
            {
                "robot_description": Command(
                    ["xacro '", urdf_file_single, "' use_gazebo:=true"]
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        condition=IfCondition(
            PythonExpression(
                [
                    "'",
                    LaunchConfiguration("urdf"),
                    "' == '' and '",
                    LaunchConfiguration("mode"),
                    "' == 'single'",
                ]
            )
        ),
    )

    robot_state_publisher_node_2_lidars = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="screen",
        parameters=[
            {
                "robot_description": Command(
                    ["xacro '", urdf_file_2_lidars, "' use_gazebo:=true"]
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        condition=IfCondition(
            PythonExpression(
                [
                    "'",
                    LaunchConfiguration("urdf"),
                    "' == '' and '",
                    LaunchConfiguration("mode"),
                    "' == '2_lidars'",
                ]
            )
        ),
    )

    robot_state_publisher_node_override = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="screen",
        parameters=[
            {
                "robot_description": Command(
                    ["xacro '", urdf_file_override, "' use_gazebo:=true"]
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        condition=IfCondition(
            PythonExpression(["'", LaunchConfiguration("urdf"), "' != ''"])
        ),
    )

    # Node to bridge messages like /cmd_vel and /odom
    gz_bridge_node_single = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=["--ros-args", "-p", f"config_file:={gz_bridge_params_path_single}"],
        output="screen",
        parameters=[
            {"use_sim_time": True},
        ],
        condition=LaunchConfigurationEquals("mode", "single"),
    )

    gz_bridge_node_2_lidars = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=["--ros-args", "-p", f"config_file:={gz_bridge_params_path_2_lidars}"],
        output="screen",
        parameters=[
            {"use_sim_time": True},
        ],
        condition=LaunchConfigurationEquals("mode", "2_lidars"),
    )

    # Node to run the EKF filter for state estimation
    ekf_node = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_filter_node",
        output="screen",
        parameters=[
            os.path.join(pkg_agv_navigation, "config", "ekf.yaml"),
            {"use_sim_time": True},
        ],
    )

    # launchDescriptionObject.add_action(rviz_node)
    # launchDescriptionObject.add_action(rviz_launch_arg)

    launchDescriptionObject = LaunchDescription()
    launchDescriptionObject.add_action(mode_arg)
    launchDescriptionObject.add_action(world_package_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(model_arg)
    launchDescriptionObject.add_action(world_launch)
    launchDescriptionObject.add_action(spawn_urdf_node)
    launchDescriptionObject.add_action(robot_state_publisher_node_single)
    launchDescriptionObject.add_action(robot_state_publisher_node_2_lidars)
    launchDescriptionObject.add_action(robot_state_publisher_node_override)
    launchDescriptionObject.add_action(gz_bridge_node_single)
    launchDescriptionObject.add_action(gz_bridge_node_2_lidars)
    launchDescriptionObject.add_action(ekf_node)

    return launchDescriptionObject
