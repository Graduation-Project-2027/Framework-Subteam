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
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():

    pkg_urdf_path = get_package_share_directory("agv_description")
    pkg_description = get_package_share_directory("agv_description")
    description_parent = os.path.dirname(pkg_description)
    pkg_gazebo_path = get_package_share_directory("agv_bringup")
    ekf_params_path = os.path.join(pkg_gazebo_path, "config", "ekf.yaml")

    gazebo_models_path, ignore_last_dir = os.path.split(pkg_urdf_path)
    # os.environ["GZ_SIM_RESOURCE_PATH"] += os.pathsep + gazebo_models_path

    if "GZ_SIM_RESOURCE_PATH" in os.environ:
        os.environ["GZ_SIM_RESOURCE_PATH"] += os.pathsep + description_parent
    else:
        os.environ["GZ_SIM_RESOURCE_PATH"] = description_parent

    rviz_launch_arg = DeclareLaunchArgument(
        "rviz", default_value="true", description="Open RViz."
    )

    mode_arg = DeclareLaunchArgument(
        "mode",
        default_value="single",
        description="Robot mode: single or 2 lidars",
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

    gz_bridge_params_path_single = os.path.join(
        pkg_gazebo_path, "config", "gz_bridge.yaml"
    )
    gz_bridge_params_path_2_lidars = os.path.join(
        pkg_gazebo_path, "config", "gz_bridge_2_lidars.yaml"
    )

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
    rviz_config_path = os.path.join(pkg_gazebo_path, "rviz", "config.rviz")
    rviz_node = Node(
        package="rviz2",
        executable="rviz2",
        name="rviz2",
        output="screen",
        arguments=["-d", rviz_config_path],
        condition=IfCondition(LaunchConfiguration("rviz")),
        parameters=[{"use_sim_time": True}],
    )

    # Spawn the URDF model using the `/world/<world_name>/create` service
    spawn_urdf_node = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=[
            "-name",
            "my_robot",
            "-topic",
            "robot_description",
            "-x",
            "-1",
            "-y",
            "3.5",
            "-z",
            "0.01",  # Initial spawn position
        ],
        output="screen",
        parameters=[
            {"use_sim_time": True},
        ],
    )

    robot_state_publisher_node_single = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        name="robot_state_publisher",
        output="screen",
        parameters=[
            {
                "robot_description": ParameterValue(
                    Command(
                        [
                            "xacro ",
                            urdf_file_single,
                            " use_gazebo:=true",
                        ]
                    ),
                    value_type=str,
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        arguments=["--ros-args", "--log-level", "warn"],
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
                "robot_description": ParameterValue(
                    Command(
                        [
                            "xacro ",
                            urdf_file_2_lidars,
                            " use_gazebo:=true",
                        ]
                    ),
                    value_type=str,
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        arguments=["--ros-args", "--log-level", "warn"],
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
                "robot_description": ParameterValue(
                    Command(
                        [
                            "xacro ",
                            urdf_file_override,
                            " use_gazebo:=true",
                        ]
                    ),
                    value_type=str,
                ),
                "use_sim_time": True,
            },
        ],
        remappings=[("/tf", "tf"), ("/tf_static", "tf_static")],
        arguments=["--ros-args", "--log-level", "warn"],
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

    ekf_node = Node(
        package="robot_localization",
        executable="ekf_node",
        name="ekf_filter_node",
        output="screen",
        parameters=[ekf_params_path, {"use_sim_time": True}],
    )

    # joint_state_publisher_gui_node = Node(
    #     package='joint_state_publisher_gui',
    #     executable='joint_state_publisher_gui',
    # )

    launchDescriptionObject = LaunchDescription()
    launchDescriptionObject.add_action(rviz_launch_arg)
    launchDescriptionObject.add_action(mode_arg)
    launchDescriptionObject.add_action(world_package_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(model_arg)
    launchDescriptionObject.add_action(world_launch)
    launchDescriptionObject.add_action(rviz_node)
    launchDescriptionObject.add_action(spawn_urdf_node)
    launchDescriptionObject.add_action(robot_state_publisher_node_single)
    launchDescriptionObject.add_action(robot_state_publisher_node_2_lidars)
    launchDescriptionObject.add_action(robot_state_publisher_node_override)
    launchDescriptionObject.add_action(gz_bridge_node_single)
    launchDescriptionObject.add_action(gz_bridge_node_2_lidars)
    launchDescriptionObject.add_action(ekf_node)
    # This tells Gazebo where to look for your meshes/models
    os.environ["GZ_SIM_RESOURCE_PATH"] = os.pathsep.join(
        [
            os.path.join(pkg_urdf_path, "meshes"),
            os.path.join(pkg_gazebo_path, "models"),
            os.environ.get("GZ_SIM_RESOURCE_PATH", ""),
        ]
    )
    return launchDescriptionObject
