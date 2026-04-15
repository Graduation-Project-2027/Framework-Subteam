# Mission Operator GUI (`mission_gui`)

## Overview
This package serves as the operator interface for the mobile manipulator. It provides a lightweight, Python-based command node that allows a human operator to dispatch high-level tasks to the robot. It translates simple user inputs (like choosing a pickup station) into formal `MissionRequest` messages, which are then sent to the `mission_manager` for execution.

## Nodes

### 1. `gui_command_node`
* **Purpose:** Prompts the user for a target location and publishes the command.
* **Publishes To:** `/mission_request` (`custom_interfaces/msg/MissionRequest`)
* **Typical Commands:** 
  * `A` or `B` (Navigate to pick-up stations)
  * `X` for `Home Point` or `Y` for `Exit Point` 

## Build Instructions
Because this is a pure Python package, it builds very quickly.
```bash
colcon build --packages-select mission_gui
source install/setup.bash
```

## Usage
To run the GUI command node, use the following command:
```bash
ros2 run mission_gui gui_command_node
```