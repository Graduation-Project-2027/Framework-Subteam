# GUIDE: AGV Bringup, Teleop, Mapping, and Navigation

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
- Dual-Lidar Scan Merging
- SLAM Toolbox Mapping & Localization
- Nav2 Navigation (MPPI Controller)
- **Web-based Mission Control (React + Vite)**
  - Live Map Visualization
  - Rack/Dock Location Management
  - Task Queue & Automated Missions
  - Remote Teleop & Lift Control

### Production Readiness
Treat the `2_lidars` mode as the standard experimental setup. Baseline single lidar mode is also stabilized.

---

## 2) First-time dependency install (once per machine)

If this is a fresh PC or fresh clone, install all system dependencies first:

```bash
cd /path/to/Framework-Subteam
bash install_dependencies.sh
```

This installs all required ROS 2 packages (Nav2, SLAM Toolbox, robot_localization, Gazebo bridge, laser_filters, rosbridge, etc.), Node.js, GUI npm dependencies, and cleans any stale build artifacts. You only need to run this **once per machine**.

---

## 3) Environment setup (every new terminal)

Go to workspace root:

```bash
cd /path/to/Framework-Subteam
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

- `mode:=single|2_lidars`
  - `single`: original robot setup (default)
  - `2_lidars`: dual-lidar setup (`2_lidars_robot.urdf.xacro` + 2-lidar bridge/config)
- `world_package:=agv_description`
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
source <workspace>/install/setup.bash

ros2 launch agv_bringup agv_gazebo.launch.py \
  mode:=single \
  world_package:=agv_description \
  world:=worlds/warehouse_v2.sdf
```

### Terminal 2: Rich TUI teleop

```bash
source /opt/ros/jazzy/setup.bash
source <workspace>/install/setup.bash

ros2 run agv_bringup rich_teleop_tui
```

TUI controls:

- `W / A / S / D`: move in x/y
- `Q / E`: rotate (yaw)
- `U / I`: lift joint up/down
- `Space`: clear active keys (stop)
- `Ctrl+C`: exit

Optional speed tuning:

```bash
ros2 run agv_bringup rich_teleop_tui --ros-args \
  -p linear_speed:=0.6 \
  -p strafe_speed:=0.6 \
  -p angular_speed:=1.2 \
  -p lift_step:=0.01
```

---

## 5) Alternate spawn flow from navigation package

This also launches world + robot + bridge + EKF:

```bash
ros2 launch agv_navigation spawn_robot.launch.py \
  mode:=single \
  world_package:=agv_description \
  world:=worlds/warehouse_v2.sdf
```

For dual lidar:

```bash
ros2 launch agv_navigation spawn_robot.launch.py \
  mode:=2_lidars \
  world_package:=agv_description \
  world:=worlds/warehouse_v2.sdf
```

Do not run this at the same time as `agv_gazebo.launch.py`.

---

## 6) Dual lidar mode details (`mode:=2l`)

When `mode:=2_lidars` is used:

- robot model switches to `2_lidars_robot.urdf.xacro`
- rear lidar is enabled (`scan_rear`)
- 2-lidar bridge config is used
- 2-lidar nav/mapping/localization configs are selected
- laser filter nodes are enabled where configured

Use this mode only when you specifically need dual-lidar experiments.

---

## 7) Mapping and navigation

These launches are verified for ROS 2 Jazzy.

### Localization

```bash
ros2 launch agv_navigation localization.launch.py mode:=single
```

or:

```bash
ros2 launch agv_navigation localization.launch.py mode:=2_lidars
```

### Mapping (SLAM)

```bash
ros2 launch agv_navigation mapping.launch.py mode:=single
```

or:

```bash
ros2 launch agv_navigation mapping.launch.py mode:=2_lidars
```

### Navigation

```bash
ros2 launch agv_navigation navigation.launch.py mode:=single
```

or:

```bash
ros2 launch agv_navigation navigation.launch.py mode:=2_lidars
```

### Navigation + SLAM combined

```bash
ros2 launch agv_navigation navigation_with_slam.launch.py mode:=single
```

or:

```bash
ros2 launch agv_navigation navigation_with_slam.launch.py mode:=2_lidars
```

---

## 8) Worlds and what to use them for

- `warehouse.sdf`: baseline warehouse world
- `warehouse_v2.sdf`: updated warehouse layout/version
- `boxed_nav_test.sdf`: dedicated obstacle-dense world for mapping/nav stress testing

Example using boxed test world:

```bash
ros2 launch agv_bringup agv_gazebo.launch.py \
  mode:=single \
  world_package:=agv_description \
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
export GZ_SIM_RESOURCE_PATH=$PWD/src/agv_description/models:$PWD/src/agv_description:$GZ_SIM_RESOURCE_PATH
export IGN_GAZEBO_RESOURCE_PATH=$PWD/src/agv_description/models:$PWD/src/agv_description:$IGN_GAZEBO_RESOURCE_PATH
```

### C) Duplicate publishers / strange nav preflight warnings

Usually means multiple launch sessions are running together.
Stop all old sessions and relaunch one stack cleanly.

### D) `Package '<name>' not found`

Re-source environment in that terminal:

```bash
source /opt/ros/jazzy/setup.bash
source <workspace>/install/setup.bash
```

---

## 12) Quick start: Web Mission Control

The mission control system provides a browser-based dashboard for high-level task management.

### Setup (First time only)

Run the installation script to set up `rosbridge-suite`, Node.js, and other dependencies:

```bash
bash src/agv_mission_control/install_deps.sh
```

### Launching the system

1. **Launch Navigation Simulation:**
   ```bash
   ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars
   ```

2. **Launch Mission Backend:**
   In a new terminal:
   ```bash
   ros2 launch agv_mission_control mission_control.launch.py
   ```

3. **Start the Web GUI:**
   In another terminal:
   ```bash
   cd src/agv_mission_control/gui
   npm run dev
   ```

4. **Access the Dashboard:**
   Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## 13) Team conventions
