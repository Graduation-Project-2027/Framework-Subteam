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
    pkg_bme_ros2_simple_arm = get_package_share_directory('robot_with_scara')
    pkg_mec_mobile_description = get_package_share_directory('mec_mobile_description')
    pkg_mec_mobile_gazebo = get_package_share_directory('mec_mobile_gazebo')
    pkg_mec_mobile_navigation = get_package_share_directory('mec_mobile_navigation')

    # Set GZ_SIM_RESOURCE_PATH
    gazebo_models_path, _ = os.path.split(pkg_mec_mobile_description)
    os.environ["GZ_SIM_RESOURCE_PATH"] = gazebo_models_path

    # Declare launch arguments
    rviz_launch_arg = DeclareLaunchArgument(
        'rviz', default_value='true',
        description='Open RViz.'
    )
    rviz_config_arg = DeclareLaunchArgument(
        'rviz_config', default_value='navigation.rviz',
        description='RViz config file'
    )
    world_arg = DeclareLaunchArgument(
        'world', default_value='world_4.sdf',
        description='Name of the Gazebo world file to load'
    )
    use_sim_time_arg = DeclareLaunchArgument(
        'use_sim_time', default_value='True',
        description='Flag to enable use_sim_time'
    )
    x_arg = DeclareLaunchArgument(
        'x', default_value='2.5',
        description='x coordinate of spawned robot'
    )
    y_arg = DeclareLaunchArgument(
        'y', default_value='2.0',
        description='y coordinate of spawned robot'
    )
    z_arg = DeclareLaunchArgument(
        'z', default_value='0.01',
        description='z coordinate of spawned robot'
    )
    yaw_arg = DeclareLaunchArgument(
        'yaw', default_value='-1.56',
        description='yaw angle of spawned robot'
    )

    # Path to URDF/Xacro file
    urdf_file_path = PathJoinSubstitution([
        pkg_mec_mobile_description, "urdf", "robots", "scara_robot_3d.urdf.xacro"
    ])

    # Controller parameters
    robot_controllers = PathJoinSubstitution([
        pkg_bme_ros2_simple_arm, 'config', 'controller_position.yaml'
    ])

    # Navigation config paths
    localization_params_path = os.path.join(
        pkg_mec_mobile_navigation, 'config', 'amcl_localization.yaml'
    )
    navigation_params_path = os.path.join(
        pkg_mec_mobile_navigation, 'config', 'navigation.yaml'
    )
    ekf_params_path = os.path.join(
        pkg_mec_mobile_navigation, 'config', 'ekf.yaml'
    )
    map_file_path = os.path.join(
        pkg_mec_mobile_navigation, 'maps', 'my_map.yaml'
    )

    # Launch Gazebo world
    world_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            os.path.join(pkg_mec_mobile_gazebo, 'launch', 'world.launch.py')
        ),
        launch_arguments={
            'world': LaunchConfiguration('world'),
        }.items()
    )

    # RViz node
    rviz_node = Node(
        package='rviz2',
        executable='rviz2',
        arguments=['-d', PathJoinSubstitution([pkg_mec_mobile_navigation, 'rviz', LaunchConfiguration('rviz_config')])],
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

    # Spawn the full robot model
    spawn_robot_node = Node(
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

    # Gazebo bridge for main topics
    gz_bridge_node = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=[
            "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
            "/cmd_vel@geometry_msgs/msg/Twist@gz.msgs.Twist",
            "/odom@nav_msgs/msg/Odometry@gz.msgs.Odometry",
            "/joint_states@sensor_msgs/msg/JointState@gz.msgs.Model",
            "/tf@tf2_msgs/msg/TFMessage@gz.msgs.Pose_V",
            "/camera/image@sensor_msgs/msg/Image@gz.msgs.Image",
            "/camera/camera_info@sensor_msgs/msg/CameraInfo@gz.msgs.CameraInfo",
            "/imu@sensor_msgs/msg/Imu@gz.msgs.IMU",
            "/scan@sensor_msgs/msg/LaserScan@gz.msgs.LaserScan",
            "/scan/points@sensor_msgs/msg/PointCloud2@gz.msgs.PointCloudPacked",
            "/cam_1/depth_image@sensor_msgs/msg/Image@gz.msgs.Image",
            "/cam_1/points@sensor_msgs/msg/PointCloud2@gz.msgs.PointCloudPacked",
            "/contact_left_finger@ros_gz_interfaces/msg/Contacts@<gz.msgs.Contacts",
        ],
        parameters=[
            {'use_sim_time': LaunchConfiguration('use_sim_time')},
        ],
        output="screen"
    )

    # Separate bridge for contact_left_finger (optional, uncomment if needed)
    # from launch.actions import ExecuteProcess
    # contact_bridge_process = ExecuteProcess(
    #     cmd=['ros2', 'run', 'ros_gz_bridge', 'parameter_bridge',
    #          '/contact_left_finger@ros_gz_interfaces/msg/Contacts@gz.msgs.Contacts',
    #          '--ros-args', '-p', 'direction:=GZ_TO_ROS'],
    #     output='screen'
    # )

    # EKF node for localization
    ekf_node = Node(
        package='robot_localization',
        executable='ekf_node',
        name='ekf_filter_node',
        output='screen',
        parameters=[
            ekf_params_path,
            {'use_sim_time': LaunchConfiguration('use_sim_time')},
        ]
    )

    # Nav2 Localization launch
    nav2_localization_launch_path = os.path.join(
        get_package_share_directory('nav2_bringup'), 'launch', 'localization_launch.py'
    )
    localization_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_localization_launch_path),
        launch_arguments={
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'params_file': localization_params_path,
            'map': map_file_path,
        }.items()
    )

    # Nav2 Navigation launch
    nav2_navigation_launch_path = os.path.join(
        get_package_share_directory('nav2_bringup'), 'launch', 'navigation_launch.py'
    )
    navigation_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(nav2_navigation_launch_path),
        launch_arguments={
            'use_sim_time': LaunchConfiguration('use_sim_time'),
            'params_file': navigation_params_path,
        }.items()
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
    launchDescriptionObject.add_action(rviz_launch_arg)
    launchDescriptionObject.add_action(rviz_config_arg)
    launchDescriptionObject.add_action(world_arg)
    launchDescriptionObject.add_action(use_sim_time_arg)
    launchDescriptionObject.add_action(x_arg)
    launchDescriptionObject.add_action(y_arg)
    launchDescriptionObject.add_action(z_arg)
    launchDescriptionObject.add_action(yaw_arg)

    # Add nodes and launches
    launchDescriptionObject.add_action(world_launch)
    launchDescriptionObject.add_action(rviz_node)
    launchDescriptionObject.add_action(robot_state_publisher_node)
    launchDescriptionObject.add_action(spawn_robot_node)
    launchDescriptionObject.add_action(gz_bridge_node)
    # launchDescriptionObject.add_action(contact_bridge_process)  # Uncomment if needed
    launchDescriptionObject.add_action(ekf_node)
    launchDescriptionObject.add_action(localization_launch)
    launchDescriptionObject.add_action(navigation_launch)
    launchDescriptionObject.add_action(joint_state_broadcaster_spawner)
    launchDescriptionObject.add_action(joint_trajectory_controller_spawner)

    return launchDescriptionObject