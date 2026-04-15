import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

def generate_launch_description():
    # Get the directories
    mission_nav_dir = get_package_share_directory('mission_navigation')
    nav2_bringup_dir = get_package_share_directory('nav2_bringup')

    # Create launch configuration variables
    use_sim_time = LaunchConfiguration('use_sim_time', default='true') 
    params_file = LaunchConfiguration('params_file', default=os.path.join(mission_nav_dir, 'config', 'nav2_params.yaml'))
    
    # Nav2 requires a map to start up correctly.
    map_yaml_file = LaunchConfiguration('map', default=os.path.join(mission_nav_dir, 'maps', 'map.yaml'))

    # Include the official Nav2 bringup launch file
    nav2_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(nav2_bringup_dir, 'launch', 'bringup_launch.py')),
        launch_arguments={
            'use_sim_time': use_sim_time,   
            'map': map_yaml_file,
            'params_file': params_file,
            'autostart': 'true'
        }.items()
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='true', description='Use simulation (Gazebo) clock'),
        DeclareLaunchArgument('params_file', default_value=os.path.join(mission_nav_dir, 'config', 'nav2_params.yaml'), description='Full path to the ROS2 parameters file to use'),
        DeclareLaunchArgument('map', default_value=os.path.join(mission_nav_dir, 'maps', 'map.yaml'), description='Full path to map yaml file to load'),
        nav2_launch
    ])