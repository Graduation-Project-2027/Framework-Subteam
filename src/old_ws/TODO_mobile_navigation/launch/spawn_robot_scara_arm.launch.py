import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution, Command
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory

def generate_launch_description():

    # Package paths
    pkg_urdf_path = get_package_share_directory('mec_mobile_description')
    pkg_gazebo_path = get_package_share_directory('mec_mobile_gazebo')
    pkg_mec_mobile_navigation = get_package_share_directory('mec_mobile_navigation')
    pkg_bme_ros2_simple_arm = get_package_share_directory('robot_with_scara')

    # Set GZ_SIM_RESOURCE_PATH
    gazebo_models_path, _ = os.path.split(pkg_urdf_path)
    os.environ["GZ_SIM_RESOURCE_PATH"] = gazebo_models_path

    # Declare launch arguments
    rviz_launch_arg = DeclareLaunchArgument(
        'rviz', default_value='true',
        description='Open RViz.'
    )
    world_arg = DeclareLaunchArgument(
        'world', default_value='world_4.sdf',
        description='Name of the Gazebo world file to load'
    )
    model_arg = DeclareLaunchArgument(
        'model', default_value='scara_robot_3d.urdf.xacro',
        description='Name of the URDF/Xacro description to load'
    )
    use_sim_time_arg = DeclareLaunchArgument(
        'use_sim_time', default_value='True',
        description='Flag to enable use_sim_time'
    )
    x_arg = DeclareLaunchArgument(
        'x', default_value='0.0',
        description='x coordinate of spawned robot'
    )
    y_arg = DeclareLaunchArgument(
        'y', default_value='0.35',
        description='y coordinate of spawned robot'
    )
    z_arg = DeclareLaunchArgument(
        'z', default_value='0.01',
        description='z coordinate of spawned robot'
    )
    yaw_arg = DeclareLaunchArgument(
        'yaw', default_value='0.0',
        description='yaw angle of spawned robot'
    )

    # Path to URDF/Xacro file
    urdf_file_path = PathJoinSubstitution([
        pkg_urdf_path, "urdf", "robots", LaunchConfiguration('model')
    ])

    # Controller parameters
    robot_controllers = PathJoinSubstitution([
        pkg_bme_ros2_simple_arm, 'config', 'controller_position.yaml'
    ])

    # Path to Gazebo bridge parameters
    gz_bridge_params_path = os.path.join(
        pkg_mec_mobile_navigation, 'config', 'gz_bridge.yaml'
    )

    # Launch Gazebo world
    world_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_gazebo_path, 'launch', 'world.launch.py')
        ),
        launch_arguments={
            'world': LaunchConfiguration('world'),
        }.items()
    )

    # RViz node
    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', os.path.join(pkg_urdf_path, 'rviz', 'rviz.rviz')],
        condition=IfCondition(LaunchConfiguration('rviz')),
        parameters=[{'use_sim_time': LaunchConfiguration('use_sim_time')}]
    )

    # Robot state publisher
    robot_state_publisher_node = Node(
        package='robot_state_publisher',
        executable='robot_state_publisher',
        name='robot_state_publisher',
        output='screen',
        parameters=[
            {'robot_description': Command(['xacro ', urdf_file_path])},
            {'use_sim_time': LaunchConfiguration('use_sim_time')}
        ],
        remappings=[
            ('/tf', 'tf'),
            ('/tf_static', 'tf_static')
        ]
    )

    # Spawn the robot model
    spawn_urdf_node = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=[
            "-name", "robot_3d",
            "-topic", "/robot_description",
            "-x", LaunchConfiguration('x'),
            "-y", LaunchConfiguration('y'),
            "-z", LaunchConfiguration('z'),
            "-Y", LaunchConfiguration('yaw')
        ],
        output="screen",
        parameters=[{'use_sim_time': LaunchConfiguration('use_sim_time')}]
    )

    # Node to bridge messages like /cmd_vel and /odom
    gz_bridge_node = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=[
            '--ros-args', '-p',
            f'config_file:={gz_bridge_params_path}'
        ],
        output="screen",
        parameters=[
            {'use_sim_time': True},
        ]
    )

    # EKF node for localization
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[
            os.path.join(pkg_mec_mobile_navigation, 'config', 'ekf.yaml'),
            {'use_sim_time': LaunchConfiguration('use_sim_time')}
        ]
    )

    # Joint state broadcaster spawner
    joint_state_broadcaster_spawner = Node(
        package='controller_manager',
        executable='spawner',
        arguments=['joint_state_broadcaster'],
        parameters=[{'use_sim_time': LaunchConfiguration('use_sim_time')}]
    )

    # Joint trajectory controller spawner
    joint_trajectory_controller_spawner = Node(
        package='controller_manager',
        executable='spawner',
        arguments=[
            'arm_controller',
            'gripper_controller',
            '--param-file', robot_controllers
        ],
        parameters=[{'use_sim_time': LaunchConfiguration('use_sim_time')}]
    )

    # Create LaunchDescription
    launchDescriptionObject = LaunchDescription()

    # Add arguments
    # launchDescriptionObject.add_action(rviz_launch_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(model_arg)
    launchDescriptionObject.add_action(use_sim_time_arg)
    launchDescriptionObject.add_action(x_arg)
    launchDescriptionObject.add_action(y_arg)
    launchDescriptionObject.add_action(z_arg)
    launchDescriptionObject.add_action(yaw_arg)

    # Add nodes
    launchDescriptionObject.add_action(world_launch)
    # launchDescriptionObject.add_action(rviz_node)
    launchDescriptionObject.add_action(robot_state_publisher_node)
    launchDescriptionObject.add_action(spawn_urdf_node)
    launchDescriptionObject.add_action(gz_bridge_node)
    launchDescriptionObject.add_action(ekf_node)
    launchDescriptionObject.add_action(joint_state_broadcaster_spawner)
    launchDescriptionObject.add_action(joint_trajectory_controller_spawner)

    return launchDescriptionObject