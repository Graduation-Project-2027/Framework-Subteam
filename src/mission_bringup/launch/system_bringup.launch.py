import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, TimerAction, DeclareLaunchArgument
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    sim_dir = get_package_share_directory('mission_simulation')
    nav_dir = get_package_share_directory('mission_navigation')

    use_sim_time = LaunchConfiguration('use_sim_time')
    
    declare_use_sim_time_cmd = DeclareLaunchArgument(
        'use_sim_time',
        default_value='true',
        description='Use simulation (Gazebo) clock if true'
    )


    simulation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(sim_dir, 'launch', 'mission_simulation.py')),
        launch_arguments={'use_sim_time': use_sim_time}.items() # Passed here
    )


    navigation_launch = TimerAction(
        period=5.0,
        actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(os.path.join(nav_dir, 'launch', 'navigation.launch.py')),
                launch_arguments={'use_sim_time': use_sim_time}.items() # Passed here
            )
        ]
    )


    lift_controller = Node(
        package='lift_control',
        executable='lift_controller_node',
        name='lift_controller_node',
        output='screen',
        parameters=[{'use_sim_time': use_sim_time}] 
    )

    mission_manager = TimerAction(
        period=8.0,
        actions=[
            Node(
                package='mission_manager',
                executable='mission_manager_node',
                name='mission_manager_node',
                output='screen',
                parameters=[{'use_sim_time': use_sim_time}] 
            )
        ]
    )

    return LaunchDescription([
        declare_use_sim_time_cmd, 
        simulation_launch,
        navigation_launch,
        lift_controller,
        mission_manager
    ])