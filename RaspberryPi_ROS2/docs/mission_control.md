# Mission control

Mission control turns the navigation stack into a rack-transport system: an operator queues
"move rack A → slot B" tasks in a web GUI and the `mission_manager` node executes them with Nav2
and closed-loop docking.

```text
Web GUI (React/Vite, :5173) ──WebSocket──> rosbridge (:9090) ──> mission_manager ──> Nav2 / cmd_vel / lift
         └──HTTP──> launcher_server.py (:8088): start/stop the whole stack
```

| Component | Location |
|---|---|
| Mission manager node | `amr_mission_control/amr_mission_control/mission_manager_node.py` |
| Parameters (source of truth) | `amr_mission_control/config/mission_manager.yaml` |
| Default locations (seed) | `amr_mission_control/config/locations.json` |
| GUI | `amr_mission_control/gui/` (`npm run dev -- --host`) |
| Launcher service | `amr_mission_control/launcher_server.py` |

Running it: see [launch.md](launch.md#navigate-and-run-missions).

## Locations

Dock, racks, drop zones and the operator-drawn lanes are edited in the GUI and stored in
`~/.ros/amr_locations.json` (outside the workspace, so rebuilds do not wipe them). The first run
seeds that file from `config/locations.json`. Every rack and zone has a `free`/`occupied` state; a
transport is only accepted from an occupied source to a free destination. Occupied racks are
published on `/rack_obstacles` so both costmaps keep the planner out of them.

## Transport sequence

1. Lower the costmap inflation (`inflation_radius_pickup`) so Nav2 is not repelled by rack legs.
2. Navigate (along the lanes if drawn, as one rounded curve) to the pickup **staging point** in the aisle,
   `pickup_approach_distance` in front of the rack.
3. **Dock** under the rack with `_dock_servo` (closed loop on the lidar-detected legs).
4. **Lift** (`/lift_joint/cmd_pos` = `lift_height`), wait `lift_wait_time`, switch to the
   carrying footprint and publish the measured legs on `/payload/leg_mask`.
5. **Extract** straight back into the aisle with `_drive_straight_closed_loop` (odom frame).
6. Navigate to the drop staging point.
7. **Insert** with `_drive_to_map_point` (map frame, exact slot position).
8. **Drop** (lift to 0), wait `drop_wait_time`, clear the payload mask.
9. **Retreat** straight out, restore the normal footprint and inflation, update rack states.

"Navigate to" tasks and "return to dock" use plain Nav2 goals.

## Closed-loop docking

Open-loop timed crawling was replaced by three feedback-driven primitives, so the moves survive
wheel slip and odometry drift on the real robot:

| Primitive | Feedback | Used for |
|---|---|---|
| `_dock_servo` | rack legs from `/scan_*_filtered`, in `base_footprint` | centering under the rack (along, lateral, yaw) |
| `_drive_to_map_point` | localization, `map` frame | exact placement at an empty slot |
| `_drive_straight_closed_loop` | EKF odometry, `odom` frame | extraction and retreat, holding the start line and heading |

The base is a **differential drive**: it cannot move sideways, so all three primitives use the same
line follower (`_follow_line`). Forward speed is proportional to the distance left along the line;
the heading reference is `-atan(k_lat · lateral_offset)`, so a lateral offset is removed by steering
back onto the line while driving, and turned off over the last `line_blend_distance` so the robot
ends square to the line.

**Leg-servo.** The rack pose stored in the map predicts the four leg positions in the robot frame.
Each prediction is matched to the nearest lidar cluster (within `dock_leg_match_gate`, clusters
farther than `dock_search_window` from the rack centre are ignored so neighbouring racks do not
interfere), and a 2D rigid fit of the matched legs gives the rack centre **and** yaw. It works with
2–4 visible legs. The robot follows the rack's centre axis; forward motion is gated by alignment,
with a floor (`dock_vx_floor`) so it keeps creeping in. If it reaches the rack centre with more than
`dock_lateral_tolerance` of lateral error (a differential drive cannot correct that in place), it
backs out `dock_retry_backoff` along the axis and re-approaches, at most `dock_max_retries` times.

**Stiction breakaway.** If the commanded speed is well above the measured speed for `stall_time`,
the command is briefly raised to `breakaway_velocity` to break free at the rack mouth.

## Carried-payload filter

A real latent-lift AMR raises the rack only a few centimetres, so the rack legs stay in the lidar
plane while carrying. Without care they are marked as obstacles around the robot (it "cages
itself") and SLAM bakes them into the map as ghost obstacles. At dock convergence the mission
manager snapshots the measured leg positions and publishes them, latched, on `/payload/leg_mask`;
`payload_filter` removes every return inside their bounding box (+ `box_margin`) from both scans.
Its output feeds the costmaps **and** the scan merger, so SLAM never sees the legs either.

## GUI commands

The GUI publishes JSON on `/mission_control/command` with an `action` field:

| Action | Effect |
|---|---|
| `add_task`, `remove_task`, `reorder_tasks` | edit the queue (transport or navigate tasks) |
| `start`, `pause`, `resume`, `stop` | queue execution |
| `estop` | cancel everything, publish zero velocity, state `ERROR`, and latch the hardware e-stop (`/estop` `true`) |
| `estop_release` | request the hardware e-stop release (`/estop` `false`) and go from `ERROR` back to `IDLE` |
| `return_to_dock` | queue a navigation to the dock and start |
| `navigate_to` | single navigation goal |
| `update_locations`, `update_dock`, `update_lanes`, `set_location_state` | edit locations |
| `teleop` | joystick velocity `lx`, `az` (scaled by `teleop_linear_scale` / `teleop_angular_scale`; `ly` is ignored) |
| `lift` | raise or lower the lift |
| `set_speed` | maximum autonomous speed (velocity smoother limit) |

On the real robot the GUI e-stop also latches the hardware e-stop through the CAN bridge service
`/estop`: the hoverboard brakes and the STM32F411 controller cuts its power
([hardware.md](hardware.md#e-stop-controller-stm32f411ceu6-body-node)). In simulation `/estop` does
not exist and the stop is software only. The release is refused while the physical button is
pressed. Release from the GUI with **Release E-stop** (shown in the top bar while the state is
`ERROR`, behind a confirmation), or with
`ros2 service call /estop std_srvs/srv/SetBool "{data: false}"` and press *Stop* / start a new task.

## GUI

The GUI (`amr_mission_control/gui/`, design notes in its `DESIGN.md`) has three areas: **Tasks**,
**Locations** and **Maps** on the left (planning), the live floor plan in the middle, and **Robot**
on the right (state, mission steps, telemetry, hold-to-drive, lift, max speed). On tablets the
Robot panel folds into the left tabs; on phones the map sits above one tabbed panel. `W`/`S` drive
and `A`/`D` turn from any tab (not while typing or in a dialog). It reconnects to rosbridge by
itself and, while there is no robot, offers the next step (launch navigation or mapping through
the launcher, or open Preview).

**System** (top bar) controls the robot computer through `launcher_server.py`: real robot or
simulation, navigation on a chosen map or mapping, RViz on/off, launch/restart/stop, and the live
`ros2 launch` output ([launch.md](launch.md#one-click-start-from-the-gui)).

**Maps** lists the saved maps (thumbnail, size, default, in use, edited, missing pose graph). New map
starts mapping; while mapping, the bar over the map shows the explored area and saves the session
under a name (pose graph + image), then offers to navigate with it. Each map can be used for
navigation, edited, renamed, duplicated, set as default, reverted to its scan, or deleted (to
`~/.ros/amr_map_trash`, with Undo). The **map editor** paints walls, free floor or unknown with a
brush or straight lines (undo/redo, `B`/`L`, `1`–`3`, `[`/`]`); saved edits are what global planning
uses next time ([launch.md](launch.md#edited-maps-and-planning)).

**Layers** (map toolbar) toggles what RViz shows, each topic subscribed only while on (CBOR over
rosbridge): occupancy map (`/map_static` or `/map`), metre grid, global and local costmaps, planned
path, footprint, merged/front/rear lidar (placed with `/tf`), dynamic obstacles (lidar hits on free
floor away from mapped walls, boxed), rack legs (`/rack_obstacles`), lanes, racks/zones/dock and the
robot trail. The lane button next to it shows or hides lanes. Choices persist in the browser.

Map: drag to pan, scroll or pinch to zoom, double-click to send the robot to a point, right-click a
rack to flip free/occupied; arrow keys pan, `+`/`-` zoom, `0` fits. Only occupied sources and free
destinations are offered when adding a transport.

**Lanes** have no sharp points. Every corner of a route, a bend inside a lane or a junction
where lanes meet, is rounded with `lane_corner_radius` (0.6 m, capped at half of the shorter
neighbouring segment). The mission manager follows the rounded route in one `FollowPath` goal instead of
stopping at every point, and falls back to one Nav2 goal per point if the controller can't
(`lane_smooth_follow: false` restores the old behaviour). The GUI draws lanes with the same geometry: rounded
turning arcs at junctions, and a new lane's ends snap onto a nearby lane (a junction point is
inserted) and crossings get a shared junction point, so lanes really connect in the routing graph.

**Preview mode** (`http://<host>:5173/?preview`, or *Open preview* on the "Robot not connected"
card) simulates the robot computer in the browser on the real map and seed layout: missions with
A* and lane routing, the launcher (mapping/navigation, logs, a map library kept in the tab), SLAM
that reveals the map from simulated lidar while you drive, costmaps, rack legs, footprint and a
person crossing an aisle as a dynamic obstacle. It needs no ROS, robot or sim and never sends
anything to hardware; use it to test the GUI or rehearse a demo.

## Parameters

`config/mission_manager.yaml` overrides the defaults in the code: **always edit the YAML**. Main
values:

| Parameter | Value | Purpose |
|---|---|---|
| `pickup_approach_distance` | 1.35 m | staging point outside the rack footprint |
| `lane_smooth_follow` / `lane_corner_radius` | true / 0.6 m | follow lane routes as one rounded curve (FollowPath) |
| `carrying_approach_distance` | 1.20 m | drop staging distance with the larger footprint |
| `inflation_radius_normal / pickup / carrying` | 0.55 / 0.25 / 0.25 m | inflation per phase |
| `dock_gain_x / y / yaw` | 0.9 / 1.1 / 1.4 | forward speed per metre, heading correction per metre of lateral offset, turn rate per radian |
| `dock_vx_max / wz_max` | 0.28 m/s, 0.6 rad/s | docking speed caps |
| `dock_pos_tolerance / dock_lateral_tolerance / dock_yaw_tolerance` | 0.03 m / 0.04 m / 0.05 rad | docking convergence |
| `dock_retry_backoff / dock_max_retries` | 0.8 m / 2 | back out and re-approach when still off-centre |
| `line_blend_distance` | 0.15 m | last stretch where only the heading is squared up |
| `dock_vx_floor` | 0.30 | minimum forward fraction while aligning |
| `breakaway_velocity`, `stall_ratio`, `stall_time` | 0.55 m/s, 0.4, 0.35 s | anti-stiction |
| `extract_clearance / retreat_clearance` | 1.15 m | straight pull-out distance |
| `lift_height`, `lift_wait_time`, `drop_wait_time` | 0.05 m, 6 s, 5 s | lift timing |
| `footprint_robot / footprint_carrying` | 0.70×0.64 m / 1.05×1.05 m | Nav2 footprints |

`payload_filter` has `box_margin` (0.12 m).

## Tuning on the real robot

The docking gains were first tuned on the old mecanum model and only checked offline for the
differential drive; re-tune them in simulation and on the hardware:

1. Start with lower docking speeds (`dock_vx_max`, `dock_wz_max`) and `breakaway_velocity`.
2. Watch the dock telemetry in the GUI feedback log (`dock along=… lat=… yaw=… legs=… cmd=… act_v=…`):
   `along` and `lat` should march to zero with `legs=4`. Frequent retries mean the Nav2 staging pose
   is too far off the rack axis or `dock_gain_y` is too low.
3. Check `/scan_front_nav` while carrying: no legs should remain around the robot.
4. Set `lift_height` and `lift_wait_time` to the real lift stroke and speed once it exists.
