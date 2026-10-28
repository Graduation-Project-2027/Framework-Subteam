# Troubleshooting and known issues

## Real robot

| Symptom | Cause / fix |
|---|---|
| `can_bridge`: *Cannot open the CAN interface* | `can0` is missing or down: `ip -details link show can0`, bring it up ([hardware.md](hardware.md#2-can-bus)). |
| *No status from the hoverboard (CAN id 1)* | Board unpowered (relay open? check `/estop/status`), power latch not bypassed, wrong `CAN_NODE_ID` vs `board_id`, bitrate mismatch, missing termination or swapped CAN_H/CAN_L. `candump can0` must show 0x181/0x281/0x381 frames. |
| *No status from the e-stop controller* | STM32F411 missing, unpowered or not sending 0x011. Only the hoverboard's own e-stop (braking, no power cut) is available. |
| Robot does not move, `/estop/status` shows `estop_active: true` | Read `cause`: release the button and/or call `/estop` with `false` (GUI: `estop_release`). Cause *host timeout* means the controller saw no `HB_CMD` for 500 ms (bridge stopped, bus problem). |
| E-stop release has no effect | The controller refuses it while the button is pressed (`button_pressed: true`). |
| Rising `can_error_counter` in the status | Bit-timing or wiring problem (the hoverboard runs on its internal RC oscillator). Lower the bitrate on all nodes. |
| Wheels stop with 3 low beeps | The board saw no command for 200 ms: the bridge died or the bus is overloaded. |
| *Hall tick jump (board restarted?)* warning | The board rebooted (relay cycled by the e-stop, or a brown-out under load) and restarted its counters; odometry re-baselines automatically. |
| Robot drives the wrong way / turns the wrong way | Wheel sign or swapped motors: run the direction test in [hardware.md](hardware.md#6-first-drive-checklist). |
| Odometry distance or heading wrong | Measure `wheel_radius` / `wheel_separation` ([hardware.md](hardware.md#parameters-to-measure)). |
| `rplidar_node`: *cannot bind to the specified serial port* | Wrong `serial_port` or user not in `dialout` (log out/in after `install_dependencies.sh`). |
| A scan appears behind/mirrored in RViz | `flip_x_axis` / `inverted` for that lidar in `hardware.yaml`. |
| Lidar sees the robot itself | Narrow `lower_angle`/`upper_angle` in `amr_mapping/config/laser_filters.yaml`. |
| `mpu9250`: *Cannot talk to the MPU9250* | Wiring, I2C disabled, user not in `i2c`, or address 0x69 (AD0 high): `sudo i2cdetect -y 1`. |
| Heading drifts | Recalibrate the gyro with the robot still (`/imu/calibrate_gyro`). Do not enable `use_mag` without a hard-iron calibration. |
| RViz on the laptop shows nothing | Same `ROS_DOMAIN_ID` on both machines, multicast allowed on the network, and no `use_sim_time` on the laptop. |
| Lift links shown without transform in RViz | Expected until the lift controller publishes `lift_joint` ([hardware.md](hardware.md#5-lift-lights-and-buzzer-planned)). |

## Simulation

| Symptom | Cause / fix |
|---|---|
| Nodes wait forever / *no clock* | `sim:=true` was passed to a launch file while Gazebo is not running (or the reverse). Launch with matching `sim` values everywhere. |
| Robot does not spawn | `robot_state_publisher` crashed (xacro error in the log) or another Gazebo instance is running. |
| Warehouse models missing | Run `gz sim` only through `bringup.launch.py`, which sets `GZ_SIM_RESOURCE_PATH`. |
| Duplicate publishers, strange Nav2 behaviour | Several stacks running at once: stop everything and start one. Mapping and navigation already include the bringup. |

## Navigation stack (fixed, kept for reference)

These were resolved during the ROS 2 Jazzy migration; keep them in mind when editing the configs.

- **MPPI motion model is case-sensitive**: `motion_model: "DiffDrive"` (exact spelling), otherwise
  *Model ... is not valid*.
- **Collision monitor crash on empty lists**: Jazzy rejects empty `polygons`/`observation_sources`.
  The config keeps all entries present but `enabled: False`, making the monitor a pass-through.
- **Inflation radius vs footprint**: `inflation_radius` (0.55 m) must exceed the footprint's
  circumscribed radius (~0.52 m), otherwise `computeCircumscribedCost` errors.
- **Blank costmaps in RViz on Nvidia**: GLSL sampler error with the `costmap` colour scheme; switch
  the Map display colour scheme to `map`.
- **Spawn pose vs map start**: the Gazebo spawn pose equals the `map_start_pose` of the `Hang1` map
  `(-1.545, 1.621, 0.014)`; change both together.
- **Scan merger start-up**: it waits for the lidar TFs instead of crashing when they are late.

## Mission control (fixed, kept for reference)

- **Parameters seemingly ignored**: `config/mission_manager.yaml` overrides the code defaults.
  Always tune in the YAML.
- **Robot "fears" going under the rack**: the staging point must lie fully outside the rack
  footprint (`pickup_approach_distance` 1.35 m) and the pickup inflation must be low (0.25 m);
  docking is done by the leg-servo, not Nav2.
- **Ghost obstacles after carrying a rack**: SLAM and the costmaps must both consume the
  payload-filtered scans (`/scan_*_nav`); the leg mask is a box, not four circles.
- **Rack dragged on retreat**: `drop_wait_time` 5 s lets the rack settle before reversing.
- **GUI lanes vanished / rack colours stuck / speed slider ignored**: fixed in the GUI state merge,
  task payload ids and velocity-smoother limit respectively.
