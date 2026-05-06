# Final Handover: AGV Navigation & Mapping Stability

This document summarizes the current stable state of the AGV project after the ROS 2 Jazzy migration and stabilization.

## 1. Project State
- **Core Base:** Mechanum-drive AGV with dual-lidar sensor fusion.
- **OS:** ROS 2 Jazzy.
- **Simulation:** Gazebo Fortress.

## 2. Key Stability Fixes (Jazzy)
- **MPPI Controller:** Fixed motion model capitalization (`"Omni"`) and parameter structure.
- **Collision Monitor:** Resolved node crashes by using a "disabled pass-through" configuration to avoid type errors on empty polygon lists in Jazzy.
- **Costmap Consistency:** Increased `inflation_radius` to `0.55m` to satisfy the circumscribed radius constraint of the robot footprint (~0.517m).
- **Scan Merging:** Stabilized the `scan_merger.py` node with TF availability checks to prevent startup crashes.
- **Pose Synchronization:** Synchronized the Gazebo spawn position with the SLAM Toolbox `map_start_pose` at `(-1.545, 1.621, 0.014)`.

## 3. Configuration & Launch
- **Unified Simulation Launch:** `agv_navigation navigation_sim.launch.py`
  - Argument `mode:=2_lidars`: Enables dual-lidar bridge, merger, and filters.
  - Argument `mode:=single`: Baseline single-lidar configuration.
- **Localization:** Both modes now use SLAM Toolbox in `localization` mode by default, loading the `Hang1` map.
- **Mapping:** To start fresh mapping, pass `map_file:=""` to the simulation launch.

## 4. Maintenance Notes
- **RViz:** If costmaps appear blank on Nvidia hardware, it is a known shader bug. Change the Color Scheme to `map` in the Map display properties.
- **File Cleanup:** Removed unused mapping-only configs (`slam_toolbox_mapping*`) from the navigation package to keep the directory clean.

## 5. Launch Commands
```bash
# Recommended baseline (2-lidar Nav + SLAM)
ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars

# Manual TUI Control
ros2 run agv_bringup rich_teleop_tui
```
