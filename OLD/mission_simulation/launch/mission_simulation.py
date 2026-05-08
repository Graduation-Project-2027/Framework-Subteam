import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

def generate_launch_description():
    # --- 1. Get Package Directories ---
    pkg_mission_sim = get_package_share_directory('mission_simulation')
    pkg_ros_gz_sim = get_package_share_directory('ros_gz_sim')

    # --- 2. Define File Paths ---
    urdf_path = os.path.join(pkg_mission_sim, 'urdf', 'mobile_manipulator.urdf')
    default_world_path = os.path.join(pkg_mission_sim, 'worlds', 'test_world.sdf')
    gazebo_config_path = os.path.join(pkg_mission_sim, 'config', 'gazebo_bridge.yaml')
    rviz_config_path = os.path.join(pkg_mission_sim, 'rviz', 'urdf_config.rviz')

    # --- 3. Declare Launch Arguments ---
    world_path = LaunchConfiguration('world_path')
    world_path_arg = DeclareLaunchArgument(
        'world_path',
        default_value=default_world_path,
        description='Path to the Gazebo world file'
    )

    # Convert XACRO/URDF to a string parameter
    robot_description = ParameterValue(Command(['xacro ', urdf_path]), value_type=str)

    # --- 4. Define Nodes and Includes ---

    # Robot State Publisher
    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{
            'robot_description': robot_description,
            'use_sim_time': True
        }]
    )

    # Gazebo Simulator Inclusion
    gz_sim_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(pkg_ros_gz_sim, 'launch', 'gz_sim.launch.py')),
        launch_arguments={'gz_args': ['-r ', world_path]}.items()
    )

    # Spawn Entity in Gazebo
    spawn_robot_node = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-topic', 'robot_description',
            '-name', 'mobile_manipulator',
            '-x', '-2.350',
            '-z', '0.3'
        ],
        output='screen'
    )

    # ROS-Gazebo Parameter Bridge
    gz_bridge_node = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            '--ros-args', 
            '-p', f'config_file:={gazebo_config_path}'
        ],
        output='screen'
    )

    # RViz2 Node
    rviz2_node = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', rviz_config_path],
        parameters=[{'use_sim_time': True}],
        output='screen'
    )

    # --- 5. Return the Launch Description ---
    return LaunchDescription([
        world_path_arg,
        robot_state_publisher_node,
        gz_sim_include,
        spawn_robot_node,
        gz_bridge_node,
        rviz2_node
    ])  