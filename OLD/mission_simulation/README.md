# Mission Simulation (`mission_simulation`)

## Overview
This package serves as the physical and environmental description layer for the mobile manipulator. It contains the robot's URDF (Unified Robot Description Format), the Gazebo simulation worlds, the RViz visualization configurations, and the ROS-Gazebo bridge parameters necessary for the digital twin to interact with the ROS 2 network.

## Key Directories

### 1. `urdf/`
* **`mobile_manipulator.urdf`**: The physical model of the robot, defining its links, joints, and Gazebo plugins (e.g., DiffDrive, JointStatePublisher, JointPositionController).

### 2. `config/`
* **`gazebo_bridge.yaml`**: The master dictionary that links Gazebo topics (like `/world/maze/model/...`) to standard ROS 2 topics (like `/joint_states` or `/odom`).

### 3. `launch/`
* Contains the scripts to load the robot description into the `robot_state_publisher`, start Gazebo, and spawn the robot into the simulated world.

### 4. `rviz/`
* **`urdf_config.rviz`**: A pre-saved RViz2 workspace layout so the operator immediately sees the robot model, map, and Nav2 costmaps upon boot.

### 5. `worlds/`
* Contains any custom `.world` files (like the maze) used for testing the robot's navigation and manipulation logic.

## Build Instructions
```bash
colcon build --packages-select mission_simulation
source install/setup.bash