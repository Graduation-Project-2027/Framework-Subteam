import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

def generate_launch_description():
    pkg_mission_sim = get_package_share_directory('mission_simulation')
    pkg_ros_gz_sim = get_package_share_directory('ros_gz_sim')

    share_dir = os.path.dirname(pkg_mission_sim)

    os.environ["GZ_SIM_RESOURCE_PATH"] = os.pathsep.join([
        share_dir,
        os.environ.get("GZ_SIM_RESOURCE_PATH", "")
    ])

    urdf_path = os.path.join(pkg_mission_sim, 'urdf', 'urdf', 'robot.urdf.xacro')
    default_world_path = os.path.join(pkg_mission_sim, 'worlds', 'world.sdf')
    gazebo_config_path = os.path.join(pkg_mission_sim, 'config', 'gazebo_bridge.yaml')
    rviz_config_path = os.path.join(pkg_mission_sim, 'rviz', 'urdf_config.rviz')

    world_path = LaunchConfiguration('world_path')
    world_path_arg = DeclareLaunchArgument(
        'world_path',
        default_value=default_world_path,
        description='Path to the Gazebo world file'
    )

    robot_description = ParameterValue(Command(['xacro ', urdf_path]), value_type=str)
    
    # 1. DEFINE the node FIRST
    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        parameters=[{
            'robot_description': robot_description,
            'use_sim_time': True
        }]
    )

    # 2. THEN wrap it in the Timer
    delayed_rsp = TimerAction(
        period=2.0,
        actions=[robot_state_publisher_node]
    )

    gz_sim_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(pkg_ros_gz_sim, 'launch', 'gz_sim.launch.py')),
        launch_arguments={'gz_args': ['-r ', world_path]}.items()
    )

    spawn_robot_node = Node(
        package='ros_gz_sim',
        executable='create',
        arguments=[
            '-topic', 'robot_description',
            '-name', 'mobile_manipulator',
            "-x", "0", "-y", "3.5", "-z", "0.01" 
        ],
        parameters=[{'use_sim_time': True}]
    )

    gz_bridge_node = Node(
        package='ros_gz_bridge',
        executable='parameter_bridge',
        arguments=[
            "/lift_joint/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double",
            "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
            "/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist",
            "/odom@nav_msgs/msg/Odometry@gz.msgs.Odometry",
            "/joint_states@sensor_msgs/msg/JointState@gz.msgs.Model",
            "/imu@sensor_msgs/msg/Imu@gz.msgs.IMU", 
            "/tf@tf2_msgs/msg/TFMessage[gz.msgs.Pose_V",
            "/scan@sensor_msgs/msg/LaserScan@gz.msgs.LaserScan",             
            "/scan/points@sensor_msgs/msg/PointCloud2@gz.msgs.PointCloudPacked",  

            '--ros-args', 
            '-p', f'config_file:={gazebo_config_path}',
        ],
        output='screen',
        parameters=[{'use_sim_time': True}]
    )

    rviz2_node = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', rviz_config_path],
        parameters=[{'use_sim_time': True}],
        output='screen'
    )

    return LaunchDescription([
        world_path_arg,
        gz_sim_include,
        gz_bridge_node,      
        spawn_robot_node,    
        delayed_rsp,        
        rviz2_node
    ])