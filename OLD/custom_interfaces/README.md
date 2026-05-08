# Custom Interfaces (`custom_interfaces`)

## Overview
This package contains the custom ROS 2 messages (`.msg`) and actions (`.action`) used to control the mobile manipulator. It acts as the central communication dictionary, ensuring the Mission Manager, the User GUI, and the physical/simulated controllers all speak the exact same language.

## Defined Interfaces

### 1. Messages (`msg/`)
* **`MissionRequest.msg`**
  * **Purpose:** Used by the Operator GUI to send a target destination to the Mission Manager.
  * **Data:** Contains the target location string (e.g., "A", "B", "HOME").

### 2. Actions (`action/`)
* **`LiftToHeight.action`**
  * **Purpose:** Used by the Behavior Tree to command the lift mechanism to move to a specific elevation.
  * **Goal:** `float64 target_height` (The desired height in meters).
  * **Result:** `bool success` (True if the lift reached the target).
  * **Feedback:** `float64 current_height` (Real-time tracking of the lift's position).

## Build Instructions
Because other packages (like `mission_manager` and the GUI) depend on these definitions, this package must be built first or alongside them:
```bash
colcon build --packages-select custom_interfaces
source install/setup.bash