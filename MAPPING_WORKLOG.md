# Mapping Worklog

## Scope
- Build a dedicated mapping package using `slam_toolbox` (not AMCL).
- Keep localization/navigation flows separate from mapping flows.

## Topics Visited
- `/clock`
- `/scan`
- `/scan_front_filtered`
- `/scan_rear_filtered`
- `/odometry/filtered`
- `/map`
- `/tf`
- `/tf_static`

## Edit Trace
- Created this root worklog first (as requested).
- Added a new package: `src/mecanum_robot_mapping`.
- Added mapping launch flow with preflight checks for live sim data.
- Added mapping simulation wrapper launch to spawn robot + run SLAM mapping.
- Added SLAM and dual-lidar filter configs in the new package.
- Fixed startup race and ROS-context shutdown issue from `_.log`:
  - Preflight now uses isolated ROS context/executor.
  - Mapping sim launch now delays SLAM startup with `mapping_start_delay_sec`.
  - Increased mapping preflight timeout default to 45s.
- Fixed topic QoS mismatch from `_.log`:
  - Preflight subscribers now use `qos_profile_sensor_data` for `/scan`, `/odometry/filtered`, and `/clock`.
  - This allows preflight to receive sensor/clock messages from bridge publishers.

## Mapping Commands
- Spawn robot in selected world:
  - `ros2 launch mecanum_robot_mapping mapping_sim.launch.py world_package:=mecanum_robot_description world:=worlds/warehouse_v2.sdf`
- Run mapping only (when robot is already spawned):
  - `ros2 launch mecanum_robot_mapping mapping.launch.py`
- Save map:
  - `ros2 run nav2_map_server map_saver_cli -f /home/som3a-station/prjs/Grad/Framework-Subteam/src/mecanum_robot_navigation/maps/map --ros-args -p use_sim_time:=true`
