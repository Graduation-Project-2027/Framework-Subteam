# Mission Navigation (`mission_navigation`)

## Overview
This package is responsible for the robot's spatial awareness and autonomous driving capabilities. It holds the pre-generated static maps of the environment, the tuning parameters for the Nav2 stack, and the launch files required to spin up localization (AMCL), path planning, and the controller servers.

## Key Directories

### 1. `maps/`
* Contains the 2D occupancy grid representations of the Gazebo simulated worlds.
* **`map.pgm`**: The image file showing walls (black), free space (white), and unknown areas (gray).
* **`map.yaml`**: The metadata file defining the map's resolution and the robot's starting origin.

### 2. `config/`
* **`nav2_params.yaml`**: The master configuration file for the Navigation 2 stack. It dictates the robot's footprint, speed limits, costmap inflation radius, and behavior tree plugins.

### 3. `launch/`
* **`navigation.launch.py`**: Launches the Nav2 stack using the provided map and parameter files. (Typically called automatically by `mission_bringup`).

## Build Instructions
```bash
colcon build --packages-select mission_navigation
source install/setup.bash