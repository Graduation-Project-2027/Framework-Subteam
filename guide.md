# GUIDE: Framework-Subteam Bringup, Teleop, Worlds, Mapping, and Navigation

This guide is for team members opening this repository for the first time.

It explains:

- what works right now,
- what is still under development,
- and exactly how to launch each flow with the correct arguments.

---

## 1) Read this first: current project state

### Working and verified

- Gazebo world launch and robot spawn
- Motion commands (`cmd_vel`) and base movement
- Rich TUI teleop (movement + lift + odom display)
- Warehouse model rendering (`warehouse` and `warehouse_v2`)

### Not fully working / not fully tested yet

- Mapping (SLAM)
- Navigation (Nav2)

Treat mapping/navigation launches as development/testing flows, not production-ready.

---

## 2) Environment setup (every new terminal)

Go to workspace root:

```bash
cd /home/som3a-station/prjs/Grad/Framework-Subteam
```

Build once after updates:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

For zsh users:

```bash
source /opt/ros/jazzy/setup.zsh
source install/setup.zsh
```

Important: if a command says package not found, you likely forgot to source `install/setup.*` in that terminal.

---

## 3) Key launch arguments

Most launch files in this repo use these:

- `mode:=single|2l`
  - `single`: original robot setup (default)
  - `2l`: dual-lidar setup (`2l_robot.urdf.xacro` + 2-lidar bridge/config)
- `world_package:=mecanum_robot_description`
- `world:=worlds/<file>.sdf`

Common world values:

- `worlds/warehouse.sdf`
- `worlds/warehouse_v2.sdf`
- `worlds/boxed_nav_test.sdf`

---

## 4) Recommended day-to-day launch flow

Use this 2-terminal flow for reliable simulation + manual control.

### Terminal 1: simulation + robot spawn

```bash
source /opt/ros/jazzy/setup.bash
source /home/som3a-station/prjs/Grad/Framework-Subteam/install/setup.bash

ros2 launch mecanum_robot_bringup mecanum_robot_gazebo.launch.py \
  mode:=single \
  world_package:=mecanum_robot_description \
  world:=worlds/warehouse_v2.sdf
```

### Terminal 2: Rich TUI teleop

```bash
source /opt/ros/jazzy/setup.bash
source /home/som3a-station/prjs/Grad/Framework-Subteam/install/setup.bash

ros2 run mecanum_robot_bringup rich_teleop_tui
```

TUI controls:

- `W / A / S / D`: move in x/y
- `Q / E`: rotate (yaw)
- `U / I`: lift joint up/down
- `Space`: clear active keys (stop)
- `Ctrl+C`: exit

Optional speed tuning:

```bash
ros2 run mecanum_robot_bringup rich_teleop_tui --ros-args \
  -p linear_speed:=0.6 \
  -p strafe_speed:=0.6 \
  -p angular_speed:=1.2 \
  -p lift_step:=0.01
```

---

## 5) Alternate spawn flow from navigation package

This also launches world + robot + bridge + EKF:

```bash
ros2 launch mecanum_robot_navigation spawn_robot.launch.py \
  mode:=single \
  world_package:=mecanum_robot_description \
  world:=worlds/warehouse_v2.sdf
```

For dual lidar:

```bash
ros2 launch mecanum_robot_navigation spawn_robot.launch.py \
  mode:=2l \
  world_package:=mecanum_robot_description \
  world:=worlds/warehouse_v2.sdf
```

Do not run this at the same time as `mecanum_robot_gazebo.launch.py`.

---

## 6) Dual lidar mode details (`mode:=2l`)

When `mode:=2l` is used:

- robot model switches to `2l_robot.urdf.xacro`
- rear lidar is enabled (`scan_rear`)
- 2-lidar bridge config is used
- 2-lidar nav/mapping/localization configs are selected
- laser filter nodes are enabled where configured

Use this mode only when you specifically need dual-lidar experiments.

---

## 7) Mapping and navigation (WIP)

These launches are available but currently considered not fully validated.

### Localization

```bash
ros2 launch mecanum_robot_navigation localization.launch.py mode:=single
```

or:

```bash
ros2 launch mecanum_robot_navigation localization.launch.py mode:=2l
```

### Mapping (SLAM)

```bash
ros2 launch mecanum_robot_navigation mapping.launch.py mode:=single
```

or:

```bash
ros2 launch mecanum_robot_navigation mapping.launch.py mode:=2l
```

### Navigation

```bash
ros2 launch mecanum_robot_navigation navigation.launch.py mode:=single
```

or:

```bash
ros2 launch mecanum_robot_navigation navigation.launch.py mode:=2l
```

### Navigation + SLAM combined

```bash
ros2 launch mecanum_robot_navigation navigation_with_slam.launch.py mode:=single
```

or:

```bash
ros2 launch mecanum_robot_navigation navigation_with_slam.launch.py mode:=2l
```

---

## 8) Worlds and what to use them for

- `warehouse.sdf`: baseline warehouse world
- `warehouse_v2.sdf`: updated warehouse layout/version
- `boxed_nav_test.sdf`: dedicated obstacle-dense world for mapping/nav stress testing

Example using boxed test world:

```bash
ros2 launch mecanum_robot_bringup mecanum_robot_gazebo.launch.py \
  mode:=single \
  world_package:=mecanum_robot_description \
  world:=worlds/boxed_nav_test.sdf
```

---

## 9) Quick health checks

List key topics:

```bash
ros2 topic list | grep -E "scan|odom|cmd_vel|lift"
```

Check scan frequency:

```bash
ros2 topic hz /scan
```

Check filtered odometry once:

```bash
ros2 topic echo /odometry/filtered --once
```

In dual-lidar mode, also verify:

- `/scan_rear`
- `/scan_front_filtered`
- `/scan_rear_filtered`

---

## 10) Troubleshooting

### A) Robot does not spawn

Checklist:

1. Confirm terminal sourced ROS + workspace setup.
2. Ensure `robot_state_publisher` did not crash in launch logs.
3. Check for `Entity creation successful` in `ros_gz_sim create` logs.
4. Make sure only one spawn stack is active.

### B) Model visuals missing or wrong

Use launch files (`world.launch.py` is already setting resource paths).
If running `gz sim` directly, export model paths manually:

```bash
export GZ_SIM_RESOURCE_PATH=$PWD/src/mecanum_robot_description/models:$PWD/src/mecanum_robot_description:$GZ_SIM_RESOURCE_PATH
export IGN_GAZEBO_RESOURCE_PATH=$PWD/src/mecanum_robot_description/models:$PWD/src/mecanum_robot_description:$IGN_GAZEBO_RESOURCE_PATH
```

### C) Duplicate publishers / strange nav preflight warnings

Usually means multiple launch sessions are running together.
Stop all old sessions and relaunch one stack cleanly.

### D) `Package '<name>' not found`

Re-source environment in that terminal:

```bash
source /opt/ros/jazzy/setup.bash
source /home/som3a-station/prjs/Grad/Framework-Subteam/install/setup.bash
```

---

## 11) Team conventions

- Prefer `mode:=single` for demos and stable baseline behavior.
- Use `mode:=2l` only for dual-lidar development/tests.
- Always pass `world` explicitly in demos to avoid ambiguity.
- Keep mapping/navigation changes isolated and tested before claiming readiness.

---

## 12) Known good demo commands

Terminal 1:

```bash
source /opt/ros/jazzy/setup.bash
source /home/som3a-station/prjs/Grad/Framework-Subteam/install/setup.bash
ros2 launch mecanum_robot_bringup mecanum_robot_gazebo.launch.py \
  mode:=single \
  world_package:=mecanum_robot_description \
  world:=worlds/warehouse_v2.sdf
```

Terminal 2:

```bash
source /opt/ros/jazzy/setup.bash
source /home/som3a-station/prjs/Grad/Framework-Subteam/install/setup.bash
ros2 run mecanum_robot_bringup rich_teleop_tui
```

This is currently the most reliable end-to-end flow.
