import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    SetEnvironmentVariable,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
    PathJoinSubstitution,
    TextSubstitution,
)
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():

    world_package_arg = DeclareLaunchArgument(
        "world_package",
        default_value="agv_description",
        description="Package that contains the world file",
    )

    world_arg = DeclareLaunchArgument(
        "world",
        default_value="worlds/warehouse_v2.sdf",
        description="World file path relative to world_package share directory",
    )

    models_package_arg = DeclareLaunchArgument(
        "models_package",
        default_value="agv_description",
        description="Package that contains model:// resources",
    )

    pkg_ros_gz_sim = get_package_share_directory("ros_gz_sim")

    gazebo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_ros_gz_sim, "launch", "gz_sim.launch.py"),
        ),
        launch_arguments={
            "gz_args": [
                PathJoinSubstitution(
                    [
                        FindPackageShare(LaunchConfiguration("world_package")),
                        LaunchConfiguration("world"),
                    ]
                ),
                TextSubstitution(text=" -r -v -v1"),
            ],
            "on_exit_shutdown": "true",
        }.items(),
    )

    gz_resource_path_env = SetEnvironmentVariable(
        name="GZ_SIM_RESOURCE_PATH",
        value=[
            PathJoinSubstitution(
                [FindPackageShare(LaunchConfiguration("models_package")), "models"]
            ),
            os.pathsep,
            FindPackageShare(LaunchConfiguration("models_package")),
            os.pathsep,
            FindPackageShare(LaunchConfiguration("world_package")),
            os.pathsep,
            PathJoinSubstitution(
                [FindPackageShare(LaunchConfiguration("world_package")), "models"]
            ),
            os.pathsep,
            EnvironmentVariable("GZ_SIM_RESOURCE_PATH", default_value=""),
        ],
    )

    ign_resource_path_env = SetEnvironmentVariable(
        name="IGN_GAZEBO_RESOURCE_PATH",
        value=[
            PathJoinSubstitution(
                [FindPackageShare(LaunchConfiguration("models_package")), "models"]
            ),
            os.pathsep,
            FindPackageShare(LaunchConfiguration("models_package")),
            os.pathsep,
            FindPackageShare(LaunchConfiguration("world_package")),
            os.pathsep,
            PathJoinSubstitution(
                [FindPackageShare(LaunchConfiguration("world_package")), "models"]
            ),
            os.pathsep,
            EnvironmentVariable("IGN_GAZEBO_RESOURCE_PATH", default_value=""),
        ],
    )

    launchDescriptionObject = LaunchDescription()

    launchDescriptionObject.add_action(world_package_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(models_package_arg)
    launchDescriptionObject.add_action(gz_resource_path_env)
    launchDescriptionObject.add_action(ign_resource_path_env)
    launchDescriptionObject.add_action(gazebo_launch)

    return launchDescriptionObject
