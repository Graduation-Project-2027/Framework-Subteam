import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node
from launch_ros.actions import LifecycleNode

def generate_launch_description():
    # Package Directories
    sim_dir = get_package_share_directory('mission_simulation')
    nav_dir = get_package_share_directory('mission_navigation')

    # 1. Hardware / Simulation Layer (Gazebo, URDF, Controllers)
    simulation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(sim_dir, 'launch', 'mission_simulation.py'))
    )

    # 2. Navigation Layer (Nav2 Stack)
    # Delayed by 5 seconds to allow Gazebo to boot up
    navigation_launch = TimerAction(
        period=5.0,
        actions=[
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(os.path.join(nav_dir, 'launch', 'navigation.launch.py'))
            )
        ]
    )

    # 3. Lift Control Layer (Custom Node)
    lift_controller = Node(
        package='lift_control',
        executable='lift_controller_node',
        name='lift_controller_node',
        output='screen'
    )

    # 4. Mission Management Layer (Lifecycle Node & Behavior Tree)
    # Delayed by 8 seconds to ensure Nav2 action servers are available
    mission_manager = TimerAction(
        period=8.0,
        actions=[
            Node(
                package='mission_manager',
                executable='mission_manager_node',
                name='mission_manager_node',
                output='screen'
            )
        ]
    )

    return LaunchDescription([
        simulation_launch,
        navigation_launch,
        lift_controller,
        mission_manager
    ])