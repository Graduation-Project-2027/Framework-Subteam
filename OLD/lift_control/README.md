# Lift Control (`lift_control`)

## Overview
This package contains the hardware/simulation interface for the robot's lift mechanism. It runs a ROS 2 Action Server that listens for commands from the Behavior Tree (the "brain") and translates them into physical joint position commands for the Gazebo simulation.

## Nodes

### 1. `lift_controller_node`
* **Purpose:** Hosts the `/lift_to_height` action server.
* **How it works:** 
  1. Receives a target height in meters.
  2. Publishes position commands to the `/lift_joint/cmd_pos` topic (which is bridged to Gazebo's `JointPositionController`).
  3. Monitors the `/joint_states` topic to track the lift's actual elevation.
  4. Returns `SUCCESS` to the Behavior Tree once the mechanism reaches the target height.
* **Action Interface:** `custom_interfaces/action/LiftToHeight`

## Build Instructions
```bash
colcon build --packages-select lift_control
source install/setup.bash