# Framework-Subteam: AGV Mobile Manipulator

This repository contains the simulation, robot description, bringup, and navigation stack for the graduation project agv mobile manipulator.

## Current status

- Working and tested:
  - Gazebo world launch + robot spawn
  - Robot motion (`cmd_vel`)
  - Rich TUI teleop + odometry monitor
  - World rendering with local warehouse assets
- Not fully working / not fully tested yet:
  - Mapping
  - Navigation (Nav2)

## Package layout

- `agv_description`: URDF/Xacro, meshes, models, worlds
- `agv_bringup`: simulation launch files, bridges, RViz, TUI teleop
- `agv_navigation`: spawn, localization, mapping, navigation launch/config

## Quick start

From workspace root:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

If you use zsh, use `setup.zsh` instead of `setup.bash`.

### Launch simulation (recommended baseline)

```bash
ros2 launch agv_bringup agv_gazebo.launch.py \
  mode:=single \
  world_package:=agv_description \
  world:=worlds/warehouse_v2.sdf
```

### Launch Rich TUI teleop

```bash
ros2 run agv_bringup rich_teleop_tui
```

Controls:

- `W/A/S/D`: translation
- `Q/E`: rotation
- `U/I`: lift up/down
- `Space`: stop movement
- `Ctrl+C`: exit

### Dual lidar mode (optional)

```bash
ros2 launch agv_bringup agv_gazebo.launch.py \
  mode:=2l \
  world_package:=agv_description \
  world:=worlds/warehouse_v2.sdf
```

## Available worlds

- `worlds/warehouse.sdf`
- `worlds/warehouse_v2.sdf`
- `worlds/boxed_nav_test.sdf`

## Full launch guide

For complete terminal-by-terminal instructions, troubleshooting, and team workflow, read:

- `guide.md`
