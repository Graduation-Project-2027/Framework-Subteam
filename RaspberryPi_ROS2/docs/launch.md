# Launching the AMR

All commands assume a sourced workspace:

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash          # from the workspace root (RaspberryPi_ROS2)
```

## Real robot vs. simulation

Every launch file defaults to the **real robot** (Raspberry Pi 5). `sim:=true` swaps the hardware
drivers for Gazebo, switches every node to the simulation clock and opens RViz.

| Argument | Default | Meaning |
|---|---|---|
| `sim` | `false` | `true`: Gazebo world + spawned robot + `ros_gz` bridge instead of `amr_hardware`, `use_sim_time` on, RViz on |
| `rviz` | value of `sim` | Open RViz. Use `rviz:=true` on the robot only if it has a display |
| `world` | `worlds/warehouse_v2.sdf` | Gazebo world, relative to `amr_description` (or absolute). Also: `worlds/boxed_nav_test.sdf` |
| `start_delay` | `10.0` sim / `0.0` real | Seconds the mapping/navigation stack waits for the base (Gazebo start-up) |

## Launch files

| Launch file | Starts |
|---|---|
| `amr_bringup bringup.launch.py` | `robot_state_publisher`, EKF, and either `amr_hardware/hardware.launch.py` or Gazebo + spawn + bridge |
| `amr_hardware hardware.launch.py` | 2x `rplidar_node`, `mpu9250_driver`, `imu_filter_madgwick`, `can_bridge` (included by bringup, not run directly) |
| `amr_mapping scan_pipeline.launch.py` | laser filters, payload filter, scan merger (included by mapping and navigation) |
| `amr_mapping mapping.launch.py` | bringup + scan pipeline + `slam_toolbox` in mapping mode |
| `amr_navigation navigation.launch.py` | bringup + scan pipeline + `slam_toolbox` localization + Nav2 |
| `amr_mission_control mission_control.launch.py` | mission manager + rosbridge + GUI dev server |
| `amr_description display_urdf.launch.py` | URDF viewer (RViz + joint sliders), desktop only |

Only one of bringup / mapping / navigation runs at a time: mapping and navigation already include
the bringup.

## Drive the robot (first test)

```bash
ros2 launch amr_bringup bringup.launch.py        # add sim:=true for Gazebo
ros2 run amr_bringup rich_teleop_tui             # second terminal
```

TUI keys: `W/S` forward/backward, `A/D` turn left/right, `U/I` lift up/down, `Space` stop,
`Ctrl+C` quit. Defaults are conservative for the real robot (0.3 m/s); raise them in the simulation:

```bash
ros2 run amr_bringup rich_teleop_tui --ros-args -p linear_speed:=1.0 -p angular_speed:=1.0
```

## Map a new site

The bundled map `Hang1` is of the **simulated** warehouse. The real site needs its own map.

**From the GUI** (needs `launcher_server.py`, see [One-click start](#one-click-start-from-the-gui)):
**Maps → New map** starts mapping. Drive slowly through the whole area with `W A S D` (works from
any tab), type a name in the **Mapping** bar over the map and press **Save map**, then **Navigate
with …**. Saved maps appear under **Maps**, where you can navigate with, edit, rename, duplicate,
set as default or delete them.

**From a terminal:**

1. Start mapping, then drive slowly through the whole area with the TUI or the GUI joystick:

   ```bash
   ros2 launch amr_mapping mapping.launch.py
   ```

   Start where the robot will normally be parked (the dock): localization later starts from the
   first pose of the map.

2. Save both formats into `src/amr_navigation/maps/` (the serialized pose graph is what
   localization loads; the image is for viewing, the GUI and global planning):

   ```bash
   ros2 service call /slam_toolbox/serialize_map slam_toolbox/srv/SerializePoseGraph \
       "{filename: '$(pwd)/src/amr_navigation/maps/Site1'}"
   ros2 run nav2_map_server map_saver_cli -f src/amr_navigation/maps/Site1
   ```

   In the simulation append `--ros-args -p use_sim_time:=true` to the `map_saver_cli` command.

3. Navigate with it (absolute path, no rebuild needed):

   ```bash
   ros2 launch amr_navigation navigation.launch.py map_file:=$(pwd)/src/amr_navigation/maps/Site1
   ```

   To make it the default, choose **Set as default** in the GUI's Maps tab (stored in
   `~/.ros/amr_launcher.json`) or change the `map_file` default in `navigation.launch.py`.

4. The GUI shows whichever map the stack runs on; nothing to convert. Optionally erase the racks
   from the image with
   `python3 src/amr_navigation/scripts/clean_map.py Site1.yaml ~/.ros/amr_locations.json`
   (writes `Site1_clean.pgm`; racks are drawn live from the mission state anyway), or paint walls
   and erase noise in the GUI map editor.

5. Re-enter dock, rack and drop-zone positions in the GUI (they are stored per robot in
   `~/.ros/amr_locations.json` and shared by every map).

### Edited maps and planning

`navigation.launch.py` serves the saved image `<map_file>.yaml` on `/map_static` (a
`map_server_static` node) and points the global costmap's static layer at it, so walls and no-go
areas painted in the GUI map editor are respected by the planner. Localization still uses the
slam_toolbox pose graph and publishes `/map`. Edits apply the next time navigation starts on that
map; **Revert edits** in the Maps tab restores the scan (`<name>.orig.pgm` keeps the original).
`use_edited_map:=false` plans on the live SLAM map instead.

## Navigate and run missions

```bash
ros2 launch amr_navigation navigation.launch.py              # [sim:=true] [map_file:=...]
ros2 launch amr_mission_control mission_control.launch.py    # [sim:=true]
```

| `navigation.launch.py` argument | Default | Meaning |
|---|---|---|
| `map_file` | `<amr_navigation>/maps/Hang1` | Serialized SLAM Toolbox map, without extension |
| `use_edited_map` | `true` | Plan on the saved image `<map_file>.yaml` (`/map_static`, editable in the GUI) instead of the live SLAM map |

| `mission_control.launch.py` argument | Default | Meaning |
|---|---|---|
| `sim` | `false` | Use the simulation clock |
| `start_gui` | `true` | Run `npm run dev -- --host` for the GUI (port 5173) |
| `open_browser` | value of `sim` | Open a local browser (the Pi is headless) |
| `rosbridge_port` | `9090` | WebSocket port used by the GUI |
| `locations_file` | `~/.ros/amr_locations.json` | Rack/dock/lane storage |

The GUI is reachable from any machine on the network at `http://<robot-ip>:5173`.

### One-click start from the GUI

`launcher_server.py` is a small HTTP service (port 8088) that the GUI uses to run the robot
computer, so nothing has to be typed on the robot after it is started once:

```bash
python3 src/amr_mission_control/launcher_server.py      # on the robot, sourced shell
cd src/amr_mission_control/gui && npm run dev -- --host
```

The **System** button in the GUI's top bar opens the stack controls: real robot or simulation,
**Navigation** (on a chosen map) or **Mapping**, **Open RViz**, Launch/Restart/Stop, and the live
`ros2 launch` output. Navigation runs `navigation.launch.py map_file:=…` + `mission_control.launch.py`;
mapping runs `mapping.launch.py` + `mission_control.launch.py` (mission control stays up for
driving and the GUI). RViz is started separately with the matching config
(`navigation.rviz` / `mapping.rviz`) and needs a display on the robot computer; on the headless Pi
use a laptop (below). The **Maps** tab manages the saved maps in `src/amr_navigation/maps/`
(override with `AMR_MAPS_DIR`); deleted maps go to `~/.ros/amr_map_trash/`.

## Working from a laptop

The robot runs headless. To watch it in RViz from a laptop on the same network:

```bash
export ROS_DOMAIN_ID=0          # same value on robot and laptop
rviz2 -d $(ros2 pkg prefix amr_navigation)/share/amr_navigation/rviz/navigation.rviz
```

Do not set `use_sim_time` on the laptop when the robot is real. If discovery does not work across
Wi-Fi, check that both machines use the same `ROS_DOMAIN_ID` and that multicast is allowed.

## Simulation notes

- The robot spawns at `(-1.545, 1.621, yaw 0.014)`, the start pose of the `Hang1` map.
- Worlds: `warehouse_v2.sdf` (default, matches `Hang1`) and `boxed_nav_test.sdf` (obstacle test).
- Gazebo Harmonic finds the warehouse models through `GZ_SIM_RESOURCE_PATH`, set by the bringup.
- The Gazebo bridge (`amr_bringup/config/gz_bridge.yaml`) publishes the same topic names as the
  real drivers, so everything above the bringup is identical in both modes.
