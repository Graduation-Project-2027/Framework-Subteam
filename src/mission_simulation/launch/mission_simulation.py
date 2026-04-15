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

    share_dir = os.path.dirname(pkg_mission_sim)
    # --- 2. Set Environment Variables for Gazebo ---
    # This tells Gazebo where to look for your meshes and models. 
    # It points to your package's share directory.
    os.environ["GZ_SIM_RESOURCE_PATH"] = os.pathsep.join([
        share_dir,
        os.environ.get("GZ_SIM_RESOURCE_PATH", "")
    ])

    # --- 3. Define File Paths ---
    # Note: I changed 'urdf','urdf' to just 'urdf' assuming standard ROS 2 structure
    urdf_path = os.path.join(pkg_mission_sim, 'urdf', 'urdf', 'robot.urdf.xacro')
    default_world_path = os.path.join(pkg_mission_sim, 'worlds', 'world.sdf')
    gazebo_config_path = os.path.join(pkg_mission_sim, 'config', 'gazebo_bridge.yaml')
    rviz_config_path = os.path.join(pkg_mission_sim, 'rviz', 'urdf_config.rviz')

    # --- 4. Declare Launch Arguments ---
    world_path = LaunchConfiguration('world_path')
    world_path_arg = DeclareLaunchArgument(
        'world_path',
        default_value=default_world_path,
        description='Path to the Gazebo world file'
    )

    # Convert XACRO/URDF to a string parameter
    robot_description = ParameterValue(Command(['xacro ', urdf_path]), value_type=str)

    # --- 5. Define Nodes and Includes ---

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
            "-x", "0", "-y", "3.5", "-z", "0.01" 
        ],
        output='screen',
        parameters=[
            {'use_sim_time': True},
        ]
    )

    # ROS-Gazebo Parameter Bridge
    gz_bridge_node = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            "/lift_joint/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double",
            "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
            "/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist",
            "/odom@nav_msgs/msg/Odometry@gz.msgs.Odometry",
            "/joint_states@sensor_msgs/msg/JointState@gz.msgs.Model",
            "/tf@tf2_msgs/msg/TFMessage@gz.msgs.Pose_V",
            "/imu@sensor_msgs/msg/Imu@gz.msgs.IMU", 
            "/scan@sensor_msgs/msg/LaserScan@gz.msgs.LaserScan",             
            "/scan/points@sensor_msgs/msg/PointCloud2@gz.msgs.PointCloudPacked",  

            '--ros-args', 
            '-p', f'config_file:={gazebo_config_path}',
        ],
        output='screen',
        parameters=[
            {'use_sim_time': True},
        ]
    )

    # RViz2 Node
    rviz2_node = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', rviz_config_path],
        parameters=[{'use_sim_time': True}],
        output='screen'
    )

    # --- 6. Return a Single Launch Description ---
    return LaunchDescription([
        world_path_arg,
        robot_state_publisher_node,
        gz_sim_include,
        spawn_robot_node,
        gz_bridge_node,
        rviz2_node
    ])