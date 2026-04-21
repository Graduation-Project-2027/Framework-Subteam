# Navigation Package Fixes — Action Log

Date: 2026-04-16

## Overview

Multiple bugs prevented navigation (AMCL + Nav2) from functioning after launch. All fixes target `mecanum_robot_navigation`, `mecanum_robot_bringup`, and `mecanum_robot_description`.

---

## Bug 1 — Spawn Position != AMCL Initial Pose (HIGH)

**File:** `src/mecanum_robot_navigation/launch/spawn_robot.launch.py:117`

**Problem:** Robot spawned at `y=3.5` in Gazebo but AMCL initial pose was `y=0.35`. The ~3m offset meant AMCL particles never overlapped the true robot position, so localization never converged.

**Fix:** Changed spawn y from `3.5` to `0.35`:
```python
"-y", "0.35",   # was: "3.5"
```

---

## Bug 2 — Preflight Timeout Too Short (HIGH)

**Files:**
- `src/mecanum_robot_navigation/launch/navigation.launch.py:179`
- `src/mecanum_robot_navigation/launch/navigation_with_slam.launch.py:187`

**Problem:** Preflight waited 20s for `/scan` and `/odometry/filtered` before starting Nav2. The warehouse_v2 world takes 30-60s to load. Preflight fired `Shutdown()` before topics were alive.

**Fix:** Raised timeout from `20.0` to `60.0` in both files.

---

## Bug 3 — Duplicate Odometry Publisher in Gazebo (MEDIUM)

**File:** `src/mecanum_robot_description/urdf/plugins/mecanum_robot.gazebo`

**Problem:** Both `MecanumDrive` and `OdometryPublisher` plugins published to Gazebo topic `odom` and `tf`. The EKF received doubled/interleaved odometry, degrading state estimation.

**Fix:** Removed the entire `OdometryPublisher` plugin block. `MecanumDrive` already publishes odom + tf correctly.

---

## Bug 4 — Missing Rear Laser Filter in localization.launch.py (MEDIUM, 2L mode)

**File:** `src/mecanum_robot_navigation/launch/localization.launch.py`

**Problem:** In 2L mode, only `scan_filter_front_node` existed. Missing rear filter meant unfiltered `/scan_rear` went straight into AMCL costmap — lift shadow appeared as a false obstacle.

**Fix:** Added `scan_filter_rear_node`:
```python
scan_filter_rear_node = Node(
    package="laser_filters",
    executable="scan_to_scan_filter_chain",
    name="scan_filter_rear",
    output="screen",
    parameters=[
        laser_filters_params_path,
        {"use_sim_time": LaunchConfiguration("use_sim_time")},
    ],
    remappings=[("scan", "/scan_rear"), ("scan_filtered", "/scan_rear_filtered")],
    condition=LaunchConfigurationEquals("mode", "2l"),
)
```

---

## Bug 5 — Stale nav/ Directory (LOW)

**Files:** `src/mecanum_robot_navigation/nav/navigation.yaml`, `nav/amcl_localization.yaml`

**Problem:** Used wrong frames (`base_link` instead of `base_footprint`) and wrong odom topic (`/odom` instead of `/odometry/filtered`). Never referenced by any launch file.

**Fix:** Deleted the entire `nav/` directory.

---

## Bug 6 — Lidar Sensor Name Collision in 2L Mode (HIGH)

**File:** `src/mecanum_robot_description/urdf/urdf/lidar.urdf.xacro:90`

**Problem:** Both front and rear lidars had `<sensor name="${prefix}lidar_sensor">`. Gazebo rejected the duplicate name — only one lidar worked.

**Fix:** Changed sensor name to include frame_id:
```xml
<sensor name="${prefix}${frame_id}_sensor" type="lidar">
```
Now front = `laser_frame_sensor`, rear = `laser_rear_frame_sensor`.

---

## Bug 7 — IMU Bridge Topic Mismatch (HIGH)

**Files:**
- `src/mecanum_robot_bringup/config/gz_bridge.yaml`
- `src/mecanum_robot_bringup/config/gz_bridge_2l.yaml`
- `src/mecanum_robot_navigation/config/gz_bridge.yaml`
- `src/mecanum_robot_navigation/config/gz_bridge_2l.yaml`

**Problem:** The IMU URDF macro publishes to Gazebo topic `imu/data`, but all 4 bridge configs subscribed to `imu`. The bridge never received IMU data, so the EKF ran without IMU input — degraded heading estimation and no gravity compensation.

**Fix:** Changed `gz_topic_name` from `"imu"` to `"imu/data"` in all 4 files.

---

## Other Changes

### World & Model Fixes
- All 8 launch files: default world changed from `warehouse.sdf` to `warehouse_v2.sdf`
- `src/mecanum_robot_description/package.xml`: Added `<gazebo_ros gazebo_model_path="${prefix}/models"/>` so Gazebo finds the model meshes
- Synced models (Distribution_Warehouse, rack, loading_zone) from `amr_description/models/`

---

## Known Remaining Issue — Map Too Small

**File:** `src/mecanum_robot_navigation/maps/map.yaml`

**Problem:** Current map is 82x81 pixels at 0.05m/pixel = **4.1m x 4.05m**. The warehouse_v2 world is approximately **60m x 60m**. AMCL cannot localize in a map that covers <1% of the environment.

**Action Required:** Re-map the warehouse_v2 world using SLAM before AMCL-based navigation can work:
```bash
# Terminal 1: Launch world + robot
ros2 launch mecanum_robot_navigation spawn_robot.launch.py

# Terminal 2: Launch SLAM + Nav2
ros2 launch mecanum_robot_navigation navigation_with_slam.launch.py

# Terminal 3: Drive robot around warehouse with teleop
ros2 run teleop_twist_keyboard teleop_twist_keyboard

# When mapping is complete, save the map:
ros2 run nav2_map_server map_saver_cli -f src/mecanum_robot_navigation/maps/warehouse_v2
```

Then update `maps/map.yaml` (or the launch file) to point to the new map.

---

## Rebuild Command

```bash
cd ~/prjs/Grad/Framework-Subteam
colcon build --packages-select mecanum_robot_navigation mecanum_robot_bringup mecanum_robot_description --symlink-install
source install/setup.bash
```
