# Framework-Subteam: AGV Mobile Robot

This repository contains the simulation, robot description, bringup, and navigation stack for the AGV mobile robot project.

## Current status

- **Fully Functional:**
  - Gazebo world launch + robot spawn
  - Robot motion (`cmd_vel`) via Mecanum Drive
  - Rich TUI teleop + odometry monitor
  - Dual-Lidar Scan Merging
  - SLAM Toolbox Mapping & Localization
  - Nav2 Navigation (MPPI Controller)
  - **Web-based Mission Control (React + Vite)**
    - Live Map Visualization
    - Rack Location Management
    - Task Queue & Management
    - Remote Teleop & Lift Control

## Package layout

- `agv_description`: URDF/Xacro, meshes, models, worlds
- `agv_bringup`: bridge configurations, RViz, TUI teleop
- `agv_navigation`: spawn, localization, mapping, navigation launch/config
- `agv_mapping`: laser processing and scan merger scripts
- `agv_mission_control`: Web GUI and high-level task management

## Quick start

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

### Launch Navigation Simulation (Recommended)

```bash
ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars
```

### Launch Web Mission Control

```bash
# Terminal A: Launch backend
ros2 launch agv_mission_control mission_control.launch.py

# Terminal B: Start GUI
cd src/agv_mission_control/gui && npm run dev
```

### Launch Rich TUI teleop

```bash
ros2 run agv_bringup rich_teleop_tui
```

Controls:
- `W/A/S/D`: translation
- `Q/E`: rotation
- `U/I`: lift up/down
- `Space`: stop
- `Ctrl+C`: exit

## Documentation

- `guide.md`: Detailed launch instructions and troubleshooting.
- `Handover.md`: Technical details and current project state.
- `NAVIGATION_FIXES.md`: Log of resolved issues for Jazzy.
