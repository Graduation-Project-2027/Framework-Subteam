from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    package_share = Path(get_package_share_directory('3dprototype_urdf'))
    urdf_path = package_share / 'urdf' / '3dprototype_urdf.urdf'
    control_urdf_path = package_share / 'urdf' / '3dprototype_urdf_control.urdf'
    rviz_config_path = package_share / 'rviz' / 'urdf.rviz'

    robot_description = urdf_path.read_text(encoding='utf-8')

    use_joint_state_gui = LaunchConfiguration('jsp_gui')
    use_rviz = LaunchConfiguration('rviz')
    use_sim_time = LaunchConfiguration('use_sim_time')
    rviz_config = LaunchConfiguration('rviz_config')

    return LaunchDescription([
        DeclareLaunchArgument(
            'jsp_gui',
            default_value='true',
            choices=['true', 'false'],
            description='Launch joint_state_publisher_gui instead of joint_state_publisher.',
        ),
        DeclareLaunchArgument(
            'rviz',
            default_value='true',
            choices=['true', 'false'],
            description='Launch RViz2 with the packaged config.',
        ),
        DeclareLaunchArgument(
            'use_sim_time',
            default_value='false',
            choices=['true', 'false'],
            description='Use simulation time if available.',
        ),
        DeclareLaunchArgument(
            'rviz_config',
            default_value=str(rviz_config_path),
            description='Absolute path to the RViz2 config file.',
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
            package='3dprototype_urdf',
            executable='scissor_mechanism_publisher.py',
            output='screen',
        ),
        Node(
            package='joint_state_publisher',
            executable='joint_state_publisher',
            condition=UnlessCondition(use_joint_state_gui),
            arguments=[str(control_urdf_path)],
            parameters=[{'use_sim_time': use_sim_time}],
            remappings=[
                ('joint_states', 'drive_joint_states'),
                ('robot_description', 'control_robot_description'),
            ],
        ),
        Node(
            package='joint_state_publisher_gui',
            executable='joint_state_publisher_gui',
            condition=IfCondition(use_joint_state_gui),
            arguments=[str(control_urdf_path)],
            parameters=[{'use_sim_time': use_sim_time}],
            remappings=[
                ('joint_states', 'drive_joint_states'),
                ('robot_description', 'control_robot_description'),
            ],
        ),
        Node(
            package='rviz2',
            executable='rviz2',
            condition=IfCondition(use_rviz),
            arguments=['-d', rviz_config],
            output='screen',
            parameters=[{'use_sim_time': use_sim_time}],
        ),
    ])
