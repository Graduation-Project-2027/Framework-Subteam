# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

The LEVERAGE team (E-JUST graduation project 2027) commissioning and testing the AMR, and presenting live demos to the graduation jury. Used on a laptop next to the robot, on a projector during demos, and on tablets/phones walking the floor.

## Product Purpose

LEVERAGE is a rack-lifting autonomous mobile robot. Mission control is the single screen used to run the robot computer (launch navigation or mapping, real robot or simulation, RViz, stop), build and manage maps (map a site, save, choose, edit, rename, duplicate, delete), plan work (define racks, drop zones, lanes and the dock on the floor plan; queue transport tasks), run it (start, pause, stop, emergency stop, return to dock) and monitor it like RViz (map, costmaps, lidar, dynamic obstacles, footprint, Nav2 path, mission step, event log), plus manual drive and lift control.

Success: an operator can queue and run a transport mission, and a jury member can understand what the robot is doing, from the screen alone.

## Fleet

The laptop runs `fleet_server.py` and manages several robots; robots only run `launcher_server.py` and the stack, all from an identical copy of the repo (identity in `~/.ros/amr_robot.json`).

- Discovery by UDP beacon (8089); a robot is managed only after it is added (first-time setup gives it a Robot ID).
- Awareness: each robot's costmaps mark the other robots from `/fleet/peers` (`fleet_layer`).
- Traffic: path reservations, priority holds (`fleet_heartbeat` / hold in `mission_manager`), deadlock breaker; lost heartbeat = hold.
- Map sync with fingerprints (laptop library ↔ robots) and live map edits, one global layout (lanes, racks, zones, occupancy), dispatch to the best free robot, per-robot settings.
- Lanes only during a task; every site has a face, the robot enters straight from its entry point on the lane. One-way lanes for loops.
- Traffic: holds, back-off along the lane / up a spur, refuges (under occupied racks when empty, free slots when carrying), hold-loop breaker.
- A new Raspberry Pi is set up from the GUI over SSH (`provision.py`): hostname = Robot ID, ROS 2 Jazzy, upload + build, boot service; deploy updates later.

## Operating Context

- Talks to ROS 2 through rosbridge (`ws://<host>:9090`): subscribes `/mission_control/state`, `/mission_control/feedback`, `/plan`; publishes JSON commands on `/mission_control/command`.
- Optional `launcher_server.py` (`http://<host>:8088`) runs the robot computer: stack modes, RViz, launcher output and the map library (`src/amr_navigation/maps`).
- Served by Vite (`npm run dev`) on the Raspberry Pi or a laptop; labs and demo rooms are brightly lit and often projected.
- Must be testable with no robot, sim or ROS running (built-in Preview mode simulates the robot computer, maps and sensors).

## Capabilities and Constraints

- Mission states: IDLE, RUNNING, PAUSED, NAVIGATING, LIFTING, DROPPING, RETURNING, ERROR, COMPLETED.
- Transport steps: navigating_to_rack, centering, lifting, navigating_to_dest, inserting, dropping. Navigate tasks: navigating.
- A transport source must be occupied and its destination free (backend rejects otherwise).
- Map: any saved map or the live SLAM grid, in world metres (no fixed image size or origin).
- Lanes: corners and junctions are rounded (`lane_corner_radius`, 0.6 m) and followed as one curve.
- Rack footprint 0.95 m; robot footprint 0.70 x 0.64 m.

## Brand Commitments

- Product name: **LEVERAGE**.
- E-JUST (Egypt-Japan University of Science and Technology) emblem and colours: white, black, red (emblem red #FF1929), gold #DE9242.

## Product Principles

1. Safety first: E-stop and robot state are always visible and unambiguous.
2. Prevent errors instead of reporting them (only offer valid task sources/destinations).
3. The floor plan is the truth; lists and map stay cross-linked.
4. Every state has a next step: disconnected, empty, error.
5. Never pretend: simulated data is always labelled as Preview and never reaches a robot.

## Accessibility & Inclusion

Keyboard operable, visible focus, 4.5:1 text contrast, occupancy encoded by shape (filled vs hollow), not colour alone; reduced motion respected; touch targets 44 px on touch devices.
