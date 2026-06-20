from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(get_package_share_directory('3dprototype_urdf'))
    ros_gz_sim_share = Path(get_package_share_directory('ros_gz_sim'))

    urdf_path = package_share / 'urdf' / '3dprototype_urdf.urdf'
    world_path = package_share / 'worlds' / 'empty.sdf'
    gz_launch_path = ros_gz_sim_share / 'launch' / 'gz_sim.launch.py'

    robot_description = urdf_path.read_text(encoding='utf-8')

    entity_name = LaunchConfiguration('entity_name')
    gz_args = LaunchConfiguration('gz_args')
    use_sim_time = LaunchConfiguration('use_sim_time')

    return LaunchDescription([
        DeclareLaunchArgument(
            'entity_name',
            default_value='3dprototype_urdf',
            description='Entity name used inside Gazebo Sim.',
        ),
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='true',
            choices=['true', 'false'],
            description='Use Gazebo simulation time.',
        ),
        DeclareLaunchArgument(
            'gz_args',
            default_value=f'-r {world_path}',
            description='Arguments passed through to Gazebo Sim.',
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(str(gz_launch_path)),
            launch_arguments={'gz_args': gz_args}.items(),
        ),
        Node(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            output='screen',
            parameters=[
                {'robot_description': robot_description},
                {'use_sim_time': use_sim_time},
            ],
        ),
        Node(
            package='ros_gz_sim',
            executable='create',
            output='screen',
            arguments=[
                '-world',
                'default',
                '-file',
                str(urdf_path),
                '-name',
                entity_name,
                '-allow_renaming',
                'true',
            ],
        ),
    ])
