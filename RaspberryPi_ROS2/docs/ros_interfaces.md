# ROS interfaces

Topic names are identical on the real robot and in Gazebo: only the publishers of the hardware
layer change.

## TF tree

```text
map ──(slam_toolbox)──> odom ──(EKF)──> base_footprint ──> base_link ─┬─> laser_frame       (front lidar)
                                                                      ├─> laser_rear_frame  (rear lidar, yaw 180°)
                                                                      ├─> imu_link
                                                                      ├─> {left,right}_wheel_link   (drive wheels)
                                                                      ├─> {front,back}_{left,right}_caster_link
                                                                      └─> lift_base_link ─> ... ─> lift_link
```

`robot_state_publisher` publishes everything below `base_footprint` from the URDF and
`/joint_states` (wheels: CAN bridge or Gazebo; lift: Gazebo, later the lift controller).

## Topics

### Hardware layer (`amr_hardware` on the robot, Gazebo + `ros_gz_bridge` with `sim:=true`)

| Topic | Type | Publisher | Used by |
|---|---|---|---|
| `/scan` | `sensor_msgs/LaserScan` | front RPLidar / Gazebo | scan pipeline |
| `/scan_rear` | `sensor_msgs/LaserScan` | rear RPLidar / Gazebo | scan pipeline |
| `/imu/data_raw` | `sensor_msgs/Imu` | `mpu9250` (real only) | `imu_filter` |
| `/imu/mag` | `sensor_msgs/MagneticField` | `mpu9250` (real only) | `imu_filter` (if `use_mag`) |
| `/imu/temperature` | `sensor_msgs/Temperature` | `mpu9250` (real only) | monitoring |
| `/imu/data` | `sensor_msgs/Imu` | `imu_filter` / Gazebo | EKF |
| `/odom` | `nav_msgs/Odometry` | `can_bridge` / Gazebo diff-drive plugin | EKF, teleop TUI |
| `/joint_states` | `sensor_msgs/JointState` | `can_bridge` (wheels) / Gazebo (wheels + lift) | `robot_state_publisher` |
| `/hoverboard/status` | `amr_interfaces/HoverboardStatus` | `can_bridge` (real only) | monitoring (`ros2 topic echo`) |
| `/estop/status` | `amr_interfaces/SafetyStatus` | `can_bridge` (real only) | monitoring |
| `/cmd_vel` | `geometry_msgs/Twist` (`linear.x`, `angular.z`) | Nav2 collision monitor, mission manager, TUI | `can_bridge` / Gazebo |
| `/lift_joint/cmd_pos` | `std_msgs/Float64` | mission manager, TUI | Gazebo lift controller (real: planned) |
| `/clock` | `rosgraph_msgs/Clock` | Gazebo (sim only) | all nodes when `sim:=true` |

### State estimation and scan pipeline

| Topic | Type | Publisher | Used by |
|---|---|---|---|
| `/robot_description` | `std_msgs/String` | `robot_state_publisher` | RViz, Gazebo spawn |
| `/odometry/filtered` | `nav_msgs/Odometry` | EKF (`ekf_filter_node`) | Nav2, mission manager |
| `/scan_front_filtered`, `/scan_rear_filtered` | `sensor_msgs/LaserScan` | `scan_filter_front/rear` (range + ±125° FOV) | payload filter, mission manager leg detection |
| `/scan_front_nav`, `/scan_rear_nav` | `sensor_msgs/LaserScan` | `payload_filter` | costmaps, scan merger |
| `/scan_merged` | `sensor_msgs/LaserScan` | `scan_merger` (360°, frame `laser_frame`) | slam_toolbox |
| `/payload/leg_mask` | `std_msgs/Float32MultiArray` (latched) | mission manager | payload filter |

### Mapping, localization, navigation

| Topic / action | Type | Publisher | Notes |
|---|---|---|---|
| `/map` | `nav_msgs/OccupancyGrid` | slam_toolbox | mapping or localization mode |
| `/map_static` | `nav_msgs/OccupancyGrid` (latched) | `map_server_static` | saved (GUI-editable) map image; global costmap static layer when `use_edited_map:=true` |
| `/rack_obstacles` | `sensor_msgs/PointCloud2` (latched) | mission manager | legs of occupied racks, both costmaps |
| `/plan` | `nav_msgs/Path` | Nav2 planner | drawn by the GUI |
| `/cmd_vel_nav` → `/cmd_vel_smoothed` → `/cmd_vel` | `geometry_msgs/Twist` | controller → velocity smoother → collision monitor | collision monitor is a pass-through |
| `/navigate_to_pose` | `nav2_msgs/action/NavigateToPose` | Nav2 BT navigator | used by the mission manager |
| `/follow_path` | `nav2_msgs/action/FollowPath` | Nav2 controller server | mission manager: rounded lane routes in one goal |

### Mission control

| Topic | Type | Direction | Notes |
|---|---|---|---|
| `/mission_control/command` | `std_msgs/String` (JSON) | GUI → mission manager | see [mission_control.md](mission_control.md#gui-commands) |
| `/mission_control/state` | `std_msgs/String` (JSON) | mission manager → GUI | 5 Hz |
| `/mission_control/feedback` | `std_msgs/String` | mission manager → GUI | log lines |

The GUI talks to ROS through `rosbridge_websocket` on port 9090; the launcher service listens on
port 8088 and the GUI dev server on 5173. Besides the mission topics, the GUI subscribes (with CBOR,
only while the matching layer is on) to `/tf`, `/tf_static`, `/map`, `/map_static`,
`/global_costmap/costmap`, `/local_costmap/costmap`, `/scan_merged`, `/scan`, `/scan_rear`,
`/rack_obstacles` and `/local_costmap/published_footprint`.

### Launcher HTTP API (`launcher_server.py`, port 8088)

| Method and path | Effect |
|---|---|
| `GET /api/status` | running, target (`real`/`sim`), mode (`navigation`/`mapping`), map, RViz, default map |
| `GET /api/logs?after=SEQ` | `ros2 launch` output since SEQ |
| `POST /api/launch?target=&mode=&map=&rviz=` | start navigation (on a map) or mapping |
| `POST /api/stop`, `POST /api/rviz?on=1\|0` | stop everything; open/close RViz |
| `GET /api/maps` | saved maps with size, pose graph, edited flag, default, in use |
| `GET`/`PUT /api/maps/NAME/pgm` | read / replace the map image (first edit keeps `NAME.orig.pgm`) |
| `POST /api/maps/save?name=` | save the running SLAM session (pose graph + image) |
| `POST /api/maps/NAME/rename\|duplicate?to=`, `/default`, `/revert`, `/delete`; `POST /api/maps/restore?id=` | library management (delete moves to `~/.ros/amr_map_trash`) |

## Services

| Service | Type | Server | Effect |
|---|---|---|---|
| `/estop` | `std_srvs/SetBool` | `can_bridge` | `true` latches the e-stop on every CAN node (hoverboard brakes, controller cuts its power), `false` requests the release |
| `/imu/calibrate_gyro` | `std_srvs/Trigger` | `mpu9250` | re-measure the gyro bias (robot still) |
| `/front_lidar/start_motor`, `/front_lidar/stop_motor` (and `/rear_lidar/...`) | `std_srvs/Empty` | `rplidar_node` | lidar motor on/off |
| `/slam_toolbox/serialize_map` | `slam_toolbox/SerializePoseGraph` | slam_toolbox | save the map for localization |
| `/local_costmap/local_costmap/set_parameters`, `/global_costmap/global_costmap/set_parameters`, `/velocity_smoother/set_parameters` | `rcl_interfaces/SetParameters` | Nav2 | used by the mission manager (footprint, inflation, speed) |

## `amr_interfaces/msg/HoverboardStatus`

10 Hz, for the drive board (`name: drive`). Fields: `header`, `name`, `node_id`, `online`, `rpm_left/right`,
`current_left/right` [A], `ticks_left/right`, `battery_voltage` [V], `battery_percent`,
`board_temperature` [°C], `error_left/right` (bitmask `ERROR_HALL_DISCONNECTED`, `ERROR_HALL_SHORT`,
`ERROR_MOTOR_STALL`), `motors_enabled`, `cmd_timeout`, `safe_state`, `estop_active`, `aux_input`,
`battery_low`, `temp_warning`, `can_error_counter`, `heartbeat`.
See [HoverboardStatus.msg](../src/amr_interfaces/msg/HoverboardStatus.msg).

## `amr_interfaces/msg/SafetyStatus`

State of the STM32F411 e-stop controller at 10 Hz. Fields: `header`, `online` (status frames
received), `estop_active`, `hoverboard_power` (relay closed), `button_pressed`, `cause`
(`CAUSE_NONE`, `CAUSE_HOST_REQUEST`, `CAUSE_BUTTON`, `CAUSE_HOST_TIMEOUT`), `heartbeat`.
See [SafetyStatus.msg](../src/amr_interfaces/msg/SafetyStatus.msg).

## Parameter files

| File | Configures |
|---|---|
| `amr_hardware/config/hardware.yaml` | lidar ports/frames, IMU bus/ranges, Madgwick filter, CAN bridge (board id, wheel geometry, limits) |
| `amr_bringup/config/ekf.yaml` | EKF: fuses `/odom` (vx, vy = 0 as the non-holonomic constraint, vyaw) and `/imu/data` (orientation, vyaw, ax) |
| `amr_bringup/config/gz_bridge.yaml` | Gazebo ↔ ROS topic bridge (sim only) |
| `amr_mapping/config/laser_filters.yaml` | per-lidar range and field-of-view filters |
| `amr_mapping/config/slam_toolbox_mapping.yaml` | SLAM in mapping mode (`/scan_merged`) |
| `amr_navigation/config/slam_toolbox_localization.yaml` | SLAM in localization mode, map start pose |
| `amr_navigation/config/navigation.yaml` | Nav2: MPPI (DiffDrive), costmaps, smoother, collision monitor |
| `amr_mission_control/config/mission_manager.yaml` | mission manager (footprints, docking gains, timings) — overrides code defaults |
