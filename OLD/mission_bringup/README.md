# Mission Bringup (`mission_bringup`)

## Overview
This package is the main entry point for the Mobile Manipulator graduation project. It contains the master launch files responsible for spinning up the entire robotic system, ensuring that the simulation, navigation stack, hardware controllers, and mission managers all boot up in the correct order.

## Launch Files

### 1. `system_bringup.launch.py`
* **Purpose:** The primary master launch file.
* **What it launches:**
  * `mission_simulation`: Starts Gazebo, loads the chosen world, and spawns the URDF.
  * `robot_state_publisher` & Gazebo Bridges: Connects the simulated physics to ROS 2 topics.
  * `nav2_bringup`: Boots the Nav2 stack for path planning and localization.
  * `mission_manager`: Starts the Lifecycle Node holding the Behavior Tree brain.
  * `lift_control`: Starts the action server for the custom lift mechanism.
  * RViz2: Opens the visualization window.

## Usage
To boot the entire system, run:
```bash
ros2 launch mission_bringup system_bringup.launch.py
```

## Mission Manager Lifecycle
The `mission_manager_node` is a Lifecycle Node, which means it needs to be transitioned through its states before it can execute its behavior tree. After launching the system, you need to configure and activate the mission manager:
```bash
ros2 lifecycle set /mission_manager_node configure
ros2 lifecycle set /mission_manager_node activate
```