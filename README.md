# 2027 Autonomous Mobile Manipulator Graduation Project

**Author:** Youssef Adel Wahba Boutros  
**GitHub:** [youssefadell11](https://github.com/youssefadell11)  

## Project Overview
This repository contains the complete ROS 2 workspace for an autonomous mobile manipulator. The system is designed to navigate complex environments (like warehouse mazes) using Nav2, dynamically avoid obstacles, precisely position itself at target stations, and operate a closed-loop lift mechanism to pick up and drop off payloads.

The robot's "brain" is powered by a robust C++ `BehaviorTree.CPP` (v4) architecture, ensuring safe, sequential execution of tasks, including real-time emergency stop (E-Stop) interrupts and physics-aware safety interlocks.

---

## Workspace Architecture

The workspace is heavily modularized into 7 distinct ROS 2 packages:

1. **`mission_bringup`**: The master conductor. Contains the primary launch files to boot the simulation, navigation stack, and control nodes simultaneously.
2. **`mission_manager`**: The core C++ Behavior Tree execution engine (Lifecycle Node).
3. **`mission_navigation`**: The Nav2 configurations, parameter tunings, and static 2D maps.
4. **`mission_simulation`**: The digital twin. Contains the robot's URDF, Gazebo world files, and ROS-Gazebo bridge configurations.
5. **`lift_control`**: A Python-based Action Server that translates BT commands into physical joint movements, tracking real-time odometry and Gazebo joint states for safety.
6. **`mission_gui`**: A lightweight Python operator interface for dispatching high-level mission commands.
7. **`custom_interfaces`**: The custom `.msg` and `.action` definitions that unify communication across the system.

---

## 🛠️ Build Instructions

Ensure you have ROS 2 and Gazebo (Ignition) installed. Open a fresh terminal and run:

```bash
cd ~/graduation_ws
# Build the entire workspace
colcon build

# Source the newly built workspace
source install/setup.bash
```

## 🚀 Execution Guide
To run a complete mission, you will need three separate terminals.

### Terminal 1: Boot the System
This command launches Gazebo, RViz2, the robot state publishers, the Nav2 stack, the lift controller, and loads the Behavior Tree node (in an unconfigured state).

```bash
cd ~/graduation_ws
source install/setup.bash
ros2 launch mission_bringup system_bringup.launch.py
```
### Terminal 2: Activate the Brain (Lifecycle Management)
Because the **'mission_manager'** is a ROS 2 Lifecycle Node, it boots up safely in an "Unconfigured" state. You must manually configure and activate it to wake up the Behavior Tree.

```bash
cd ~/graduation_ws
source install/setup.bash

# 1. Load the XML trees and connect to the Action Servers
ros2 lifecycle set /mission_manager_node configure

# 2. Activate the node to begin listening for GUI commands
ros2 lifecycle set /mission_manager_node activate
```

### Terminal 3: Run the GUI
With the simulation running and the brain activated, open the operator interface to send your robot to work.

```bash
cd ~/graduation_ws
source install/setup.bash
ros2 run mission_gui gui_command_node
```

**Note:** Available Commands:

Type **'A'** or **'B'** to send the robot to a pick-up station. The robot will autonomously drive to the location, lift the payload, navigate to the EXIT **'(Y)'**, drop the payload, and return HOME **'(X)'**.

