# Mission Manager (`mission_manager`)

## Overview
This package acts as the central intelligence (the "Brain") of the mobile manipulator. It uses the `BehaviorTree.CPP` (v4) framework to coordinate complex sequences of actions, ensuring the robot navigates, lifts payloads, and returns home in the correct order.

## Core Components

### 1. The Behavior Tree (`behavior_trees/mission_tree.xml`)
* Defines the high-level logic flow of the robot. 
* **Current Sequence:** 
  * Navigate to Pick-up -> Lift Payload -> Navigate to Exit (Y) -> Drop Payload -> Return Home.

### 2. Custom C++ Nodes (`include/mission_manager/`)
* **`Maps_to_node.hpp`:** An Action Client that translates behavior tree commands into `nav2_msgs/action/NavigateToPose` goals.
* **`lift_object_node.hpp`:** An Action Client that sends `custom_interfaces/action/LiftToHeight` goals to the `lift_controller` hardware/simulation node.

### 3. The Executor (`src/mission_manager_node.cpp`)
* A ROS 2 Lifecycle Node that loads the XML tree, registers the custom C++ nodes, and continuously ticks the tree to execute the mission.

## Build Instructions
```bash
colcon build --packages-select mission_manager
source install/setup.bash