# Mapping Worklog

## Scope
- Build a dedicated mapping package using `slam_toolbox` (not AMCL).
- Keep localization/navigation flows separate from mapping flows.

## Topics Visited
- `/clock`
- `/scan`
- `/scan_merged`
- `/odom`
- `/tf`
- `/tf_static`

## Edit Trace
- Migrated package to `agv_mapping`.
- Fixed startup race and ROS-context shutdown issue.
- Stabilized `scan_merger` with TF availability checks.
- Synchronized Gazebo spawn pose with SLAM localization start pose.

## Mapping Commands
- Spawn robot and run mapping simulation:
  - `ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars` (The navigation sim now includes SLAM bringup).
- Save map:
  - `ros2 run nav2_map_server map_saver_cli -f /home/loay/Framework-Subteam/src/agv_navigation/maps/Hang1 --ros-args -p use_sim_time:=true`
