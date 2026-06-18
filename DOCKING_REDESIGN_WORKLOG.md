# AGV Rack Docking Redesign — Session Worklog (2026-06-18)

End-to-end rebuild of the warehouse AGV's rack pickup/transport/drop behaviour,
from open-loop dead-reckoning to a fully closed-loop, sensor-driven pipeline,
plus the sim-physics and perception fixes uncovered while testing it in Gazebo.

Robot: mecanum (holonomic) AGV, 2× lidar (front+rear), lift mechanism, ROS 2
Jazzy + Nav2 (MPPI) + slam_toolbox (localization) + Gazebo.

Primary file: `src/agv_mission_control/agv_mission_control/mission_manager_node.py`

---

## 1. Starting symptoms (user report)

- Robot fails to center itself under the rack; unreliable.
- Creeps in/out of the rack perpendicular to the aisle but fails to fully
  extract the rack or place it exactly at the drop-off — leaves it in the aisle.
- When approaching the rack it "fears" going under it.
- Aisle lanes hard to see in the GUI (separate frontend issue, not addressed).

## 2. Root-cause diagnosis (before any change)

| Symptom | Root cause | Location |
|---|---|---|
| "Fears going under the rack" | Pickup staging at **0.55 m** put the robot footprint *overlapping* the near legs (legs at 0.475 m, robot half-length 0.35 m). MPPI `CostCritic` (collision_cost 1e6, consider_footprint) refuses. | `pickup_approach_distance` |
| Same | The **source rack stayed marked as a lethal obstacle** during approach. | `_publish_rack_obstacles` only excluded the carried rack *after* lift |
| Centering unreliable | Leg-detect window too tight (`leg_detect_dist_max=0.75` vs leg distance 0.67 m), needed all 4 legs, no yaw correction | `_detect_rack_legs` |
| "Leaves it in the aisle" / inexact | `_crawl` was **open-loop timed dead-reckoning** (`duration = distance/speed`, no feedback); mecanum slips | `_crawl` |

## 3. The redesign — three closed-loop primitives

Replaced `_crawl` + `_center_under_rack` with feedback-driven moves (all survive
wheel slip / odom drift, so they port to the real robot):

- **`_dock_servo`** — pickup centering. Servos on the *detected legs*:
  `_estimate_rack_in_base` predicts the 4 leg positions from the map pose,
  matches each to the nearest lidar cluster, and solves a 2D Procrustes fit →
  refined rack centre **and yaw** in the base frame. Works with 2–4 visible
  legs, rejects adjacent-rack clutter via a search window. Holonomic P-control
  nulls lateral + yaw error while gating forward motion on alignment.
- **`_drive_to_map_point`** — drop insertion. Holonomic P-servo onto the exact
  map coordinate (no legs to servo on at an empty slot).
- **`_drive_straight_closed_loop`** — extract/retreat. Odom straight move with
  lateral + yaw hold so the carried rack doesn't swing.

Transport sequence rewritten to: nav→staging → `_dock_servo` → lift → extract →
nav→drop-staging → `_drive_to_map_point` → drop → retreat. Source rack excluded
from the costmap during approach via `self._docking_target_id`. Pickup staging
raised so the footprint clears the legs.

## 4. Iterative fixes found while testing in Gazebo

### Fix A — Dock servo stalled far from the rack
**Problem:** Servo "locked onto legs" then timed out without converging. The
alignment gate `1 − |e_y|/0.10 − |e_th|/0.12` hit zero at the error Nav2 leaves
at staging, so forward velocity was ~0; the robot wiggled but never drove in.
**Resolution:** Gate now penalises only error *beyond* tolerance and has a floor
(`dock_vx_floor=0.30`) so it always creeps inward while squaring up. Loosened
tolerances to realistic lidar levels (3 cm / 0.05 rad). Added a live telemetry
line (`dock e=… cmd_v=… act_v=…`).

### Fix B — Confirmed the velocity pipeline is clean (no contention)
**Investigation:** Robot moved at ~1/10 of commanded speed. Verified topology:
`controller→/cmd_vel_nav→velocity_smoother→/cmd_vel_smoothed→collision_monitor→
/cmd_vel→robot`; mission_manager publishes `/cmd_vel` directly. During docking
the controller is idle, so the smoother/collision chain is silent — **no
contention, no feedback loop**. Added a `/odometry/filtered` subscription to log
measured `act_v`.

### Fix C — Wheels are fine; the slowness was stiction + a weak lift
**Finding:** `act_v` tracks `cmd_v` *exactly* once the robot is moving — so wheel
friction (`mu=2.0, mu2=0.0`) was **left untouched**. The real causes:
- **Stiction catch** (~5 s) at the rack entrance, then it breaks free.
- **Lift too weak:** `lift_range` was 0.25 m → rack rose only ~0.18 m, so the
  legs stayed in the lidar plane (lidar z ≈ 0.186 m) and **scraped on extraction**.

### Fix D — Stiction breakaway
**Resolution:** Added `_breakaway`: when commanding motion but measured `act_v`
≈ 0 for `stall_time`, punch the command up to `breakaway_velocity` (0.45 m/s) to
break free, then resume normal control. Wired into both `_dock_servo` and
`_drive_straight_closed_loop`. No more manual pushing through the entrance catch.

### Fix E — Realistic lift + the carried-leg problem
**Key realisation (user):** Lifting the legs *above* the scanner is a sim-only
cheat — a real latent-lift AGV raises the rack only a couple cm, so the legs
**always stay in the lidar plane** while carrying. The robot must *ignore its own
payload*, not lift it out of the beam.
**Resolution:**
- Lift set to a realistic `lift_range = 0.20 m` (rack rises ~0.13 m: off the
  floor, no scrape, but legs remain in the scanner plane like the real robot).
- New node **`payload_filter`** (`src/agv_mapping/scripts/payload_filter.py`):
  subscribes the filtered scans, republishes `/scan_front_nav` + `/scan_rear_nav`,
  and while carrying deletes returns inside the **carried-rack bounding box** in
  the base frame. Pure pass-through when not carrying.
- The mask is **self-calibrated**: mission_manager snapshots the *measured* leg
  positions at dock convergence and publishes them on `/payload/leg_mask`
  (latched), so it masks where the legs really are. Box mask (not 4 circles) is
  robust to small leg shifts and lidar grazing.

### Fix F — Ghost static obstacles failed return-to-dock
**Problem:** After a successful R1→R8 transport, return-to-dock failed
(`status 6`) due to ghost obstacles all over the map. Cause: only the **costmap**
input was payload-filtered; **SLAM** (`slam_toolbox`, localization, reads
`/scan_merged`) and the global obstacle layer still saw leaked legs → baked a
trail of ghost obstacles.
**Resolution:**
- Switched the leg mask from 4 circles to a **bounding box** (no leak).
- Routed `scan_merger` (which feeds `/scan_merged` → SLAM) through the
  payload-filtered scans `/scan_*_nav`, so SLAM's matcher never sees the legs.

### Fix G — Rack dragged on retreat / no settle time
**Resolution:** `drop_wait_time` 2.0 → **5.0 s** so the rack fully lowers and
settles before the robot reverses out.

### Fix H — Nav2 "fears to go in" during the pickup approach
**Problem:** Nav2 crept/stalled (~80 s) approaching the pickup staging — MPPI
repelled by the rack legs its own lidar sees (source rack is excluded from
`/rack_obstacles`, but the live scan still marks the legs).
**Resolution:** Lowered the pickup-approach inflation (`inflation_radius_pickup`
0.40 → **0.25**). `dock_servo` (not Nav2) does the precise under-rack drive, so
Nav2 must not fear the legs.

## 5. Data flow (final)

```
lidars → laser_filters → /scan_{front,rear}_filtered
                              │
                              ▼
                        payload_filter  ←── /payload/leg_mask (mission_manager,
                              │                latched, box from measured legs)
            ┌─────────────────┴─────────────────┐
            ▼                                     ▼
   /scan_{front,rear}_nav                   /scan_{front,rear}_nav
            │                                     │
            ▼                                     ▼
   Nav2 costmaps (obstacle/voxel)          scan_merger → /scan_merged → slam_toolbox
```

Velocity: Nav2 `controller→/cmd_vel_nav→velocity_smoother→/cmd_vel_smoothed→
collision_monitor→/cmd_vel`. During docking/closed-loop moves, `mission_manager`
publishes `/cmd_vel` directly (Nav2 idle → no contention).

## 6. Key tunable parameters (mission_manager)

| Param | Value | Purpose |
|---|---|---|
| `pickup_approach_distance` | 1.35 | Nav2 stops in clear aisle; dock_servo covers the rest |
| `inflation_radius_pickup` | 0.25 | Don't let Nav2 fear the rack legs on approach |
| `dock_gain_x/y/yaw` | 0.9/1.1/1.4 | Dock servo P-gains |
| `dock_vx_max/vy_max` | 0.22/0.18 | Dock servo speed caps |
| `dock_vx_floor` | 0.30 | Min forward fraction (never stall while aligning) |
| `dock_pos/yaw_tolerance` | 0.03 / 0.05 | Dock convergence |
| `breakaway_velocity` | 0.45 | Stiction punch-through |
| `stall_speed_eps`, `stall_time` | 0.03, 0.6 | Stall detection (cmd>eps, act<eps for stall_time) |
| `extract/retreat_clearance` | 1.15 | Straight pull-out distance |
| `drive_vmax`, `drive_timeout` | 0.25, 45 | Closed-loop straight/point drive |
| `drop_wait_time` | 5.0 | Settle the rack before reversing |
| `lift_height` | 0.20 | Matches URDF `lift_range` |

payload_filter: `box_margin` 0.12 m. URDF (`lift.urdf.xacro`): `lift_range` 0.20,
lift velocity 0.20.

## 7. Build & test

```bash
colcon build --packages-select agv_mission_control agv_mapping agv_navigation agv_description --symlink-install
# URDF + config + new node changed → full sim restart:
ros2 launch agv_navigation navigation_sim.launch.py
ros2 launch agv_mission_control mission_control.launch.py
# Watch:
ros2 topic echo /mission_control/feedback     # dock telemetry + step logs
ros2 topic hz /scan_front_nav                  # payload filter alive
```

Watch-fors: dock telemetry `e_x` marching to 0 with `legs=4`; `cmd_v` spiking to
0.45 on a stiction catch (breakaway); payload_filter logging `Payload mask
ACTIVE: box …` on lift; no carried legs / ghost trail in RViz while carrying.

## 8. Known follow-ups / real-robot notes

- `_crawl` and `_center_under_rack` remain in the file but are **unused**
  (deprecated reference); safe to delete later.
- All docking gains/tolerances are ROS params — retune on hardware.
- The payload box mask blanks ~the rack footprint around the robot while
  carrying; safe because nothing real can be under the carried rack, but keep
  `box_margin` modest so near-field obstacles just outside the rack stay visible.
- GUI aisle-lane rendering is a separate frontend issue (not addressed).
