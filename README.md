# Framework-Subsystem: Mecanum Mobile Manipulator

This subsystem serves as the core integration layer for the **GP25 Graduation Project**. 
It manages the robot's physical description, simulation environment, and the communication bridge between ROS 2 and Gazebo.

## 🛠️ System Overview
* **Robot Name**: Mecanum Mobile Manipulator
* **OS**: Ubuntu 24.04 (Noble) 
* **ROS 2 Version**: Jazzy Jalisco
* **Simulator**: Gazebo Sim (Harmonic)

---

## 📂 Project Structure
| Package | Description |
| :--- | :--- |
| **mecanum_robot_description** | Contains URDF/Xacro files, STL meshes, and RViz configurations. |
| **mecanum_robot_bringup** | Contains launch scripts, Gazebo world files (`.sdf`), and bridge parameters. |
| **mecanum_robot_navigation** | Contains  mobile navigation pkg. |

---

## 🚀 Getting Started

### 1. Installation & Build
Ensure you are in your workspace root and have sourced the ROS 2 environment.

```bash
# Build the workspace
cd ~/gp_ws
colcon build 
source install/setup.bash

```

### 2. Launch Simulation

This command launches Gazebo, spawns the robot at the designated coordinates, and starts RViz.

```bash
ros2 launch mecanum_robot_bringup mecanum_robot_gazebo.launch.py

```

### 3. Teleop Control

To drive the robot manually using your keyboard, open a new terminal and run:

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard

```

**Controls:**

* **i / ,** : Forward / Backward
* **j / l** : Rotation (Yaw)
* **u / o** : Lateral Strafe (Left / Right)
* **k** : Stop

---

## 📡 Sensor Integration

The framework is pre-configured to bridge the following data:

* **Lidar**: `/scan` (Sensor to ROS)
* **IMU**: `/imu` (Sensor to ROS)
* **Odometry**: `/odom` (Bidirectional)
* **Velocity**: `/cmd_vel` (ROS to Gazebo)

---
