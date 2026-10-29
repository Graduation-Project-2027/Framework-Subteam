# LEVERAGE AMR: user guide

How to set up, start and use everything, from a first look in Preview to running rack transports
on the real robot. For the full feature list see [FEATURES.md](FEATURES.md); for every launch
argument see [RaspberryPi_ROS2/docs/launch.md](RaspberryPi_ROS2/docs/launch.md).

**Contents**

1. [Try it without a robot (Preview)](#1-try-it-without-a-robot-preview)
2. [First-time setup](#2-first-time-setup)
3. [Start the robot and open the GUI](#3-start-the-robot-and-open-the-gui)
4. [The screen at a glance](#4-the-screen-at-a-glance)
5. [Launch, stop and RViz (System)](#5-launch-stop-and-rviz-system)
6. [Map a new site](#6-map-a-new-site)
7. [Manage saved maps](#7-manage-saved-maps)
8. [Edit a map](#8-edit-a-map)
9. [Set up the dock, racks and drop zones](#9-set-up-the-dock-racks-and-drop-zones)
10. [Draw lanes](#10-draw-lanes)
11. [Plan and run missions](#11-plan-and-run-missions)
12. [Drive by hand, lift and speed](#12-drive-by-hand-lift-and-speed)
13. [Layers: costmaps, lidars, obstacles](#13-layers-costmaps-lidars-obstacles)
14. [Emergency stop](#14-emergency-stop)
15. [Simulation (Gazebo)](#15-simulation-gazebo)
16. [Keyboard shortcuts](#16-keyboard-shortcuts)
17. [Troubleshooting](#17-troubleshooting)
18. [Upgrading from the old AGV names](#18-upgrading-from-the-old-agv-names)
19. [Run a fleet (several robots, one laptop)](#19-run-a-fleet-several-robots-one-laptop)

---

## 1. Try it without a robot (Preview)

Preview runs a simulated robot inside the browser. Nothing is sent to hardware, and no ROS,
robot or simulator is needed. Use it to learn the interface or to give a demo.

```bash
cd RaspberryPi_ROS2/src/amr_mission_control/gui
npm install          # first time only (Node.js 18+)
npm run dev
```

Open <http://localhost:5173/?preview>. Without `?preview`, the GUI looks for a robot and offers
**Open preview** on the "Robot not connected" card. The gold **Preview** badge in the top bar
shows you are in Preview; click its × to leave.

Everything in this guide works in Preview: launching mapping or navigation, saving and editing
maps (kept in the browser tab), transports, lanes, layers and manual driving.

![Preview with a queued transport](RaspberryPi_ROS2/images/gui-overview-preview.png)

## 2. First-time setup

**On the robot** (Raspberry Pi 5, Ubuntu 24.04 + ROS 2 Jazzy):

```bash
cd RaspberryPi_ROS2
bash install_dependencies.sh            # once per machine, then log out and back in
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
```

Wire and configure the CAN bus, lidars and IMU once before the first drive:
[docs/hardware.md](RaspberryPi_ROS2/docs/hardware.md) (includes a first-drive checklist).

**On a development PC with the simulation:** the same, with `bash install_dependencies.sh --sim`.

**Motor controller:** flash `VARIANT_CAN` from [Hoverboard_Firmware](Hoverboard_Firmware/README.md)
with PlatformIO or the Hoverboard Firmware Tool.

## 3. Start the robot and open the GUI

After every boot, in a sourced shell on the robot:

```bash
cd RaspberryPi_ROS2
python3 src/amr_mission_control/launcher_server.py                 # terminal 1: launcher (port 8088)
cd src/amr_mission_control/gui && npm run dev -- --host            # terminal 2: GUI (port 5173)
```

Open `http://<robot-ip>:5173` on any laptop, tablet or phone on the same network. From now on
everything (navigation, mapping, RViz, maps) is started from the GUI.

<details>
<summary>Starting from a terminal instead</summary>

```bash
ros2 launch amr_navigation navigation.launch.py              # [map_file:=...] [sim:=true]
ros2 launch amr_mission_control mission_control.launch.py    # [sim:=true]
```

`mission_control.launch.py` also starts rosbridge (port 9090) and the GUI dev server.
</details>

## 4. The screen at a glance

| Area | What it holds |
|---|---|
| Top bar | Connection or Preview badge, **System** (stack status), robot state, Start / Pause / Stop / Return to dock, **E-stop** |
| Left panel | **Tasks** (plan and queue), **Locations** (dock, racks, drop zones, lanes), **Maps** (library and editor) |
| Centre | Live floor plan: map, robot, racks, lanes, path, sensors; map tools on the left edge, legend and scale at the bottom |
| Right panel | Robot state and mission steps, telemetry, manual drive, lift, max speed |
| Bottom | Event log (click the title to collapse) |

On a tablet the Robot panel becomes a tab on the left; on a phone the map sits above one tabbed
panel.

## 5. Launch, stop and RViz (System)

![System sheet](RaspberryPi_ROS2/images/gui-system-sheet.png)

1. Click **System** in the top bar.
2. Pick **Real robot** or **Simulation**.
3. Pick **Navigation** and a map, or **Mapping**.
4. Press **Launch**. The status goes Starting → Running and the `ros2 launch` output streams in
   the sheet. The top bar shows, for example, `Navigation · Hang1`.
5. **Restart** relaunches with new choices; **Stop** shuts the whole stack down (asks first).
6. **RViz** toggles RViz on the robot computer with the matching configuration. It needs a
   display there; on the headless Pi, run RViz on a laptop instead
   ([launch.md › Working from a laptop](RaspberryPi_ROS2/docs/launch.md#working-from-a-laptop)).
   The GUI's own **Layers** menu (section 13) shows the same data without RViz.

## 6. Map a new site

The bundled map `Hang1` is the simulated warehouse; a real site needs its own map.

1. Park the robot where it normally docks (localization later starts from the map's first pose).
2. **Maps → New map** (or System → Mapping → Launch).
3. Drive slowly through the whole area with `W A S D` (works from any tab) or the drive pad.
   Revisit the start to close loops. The **Mapping** bar shows the explored area growing.

   ![Mapping in progress](RaspberryPi_ROS2/images/gui-mapping-live.png)

4. Type a name (letters, digits, `-`, `_`, up to 40 characters) and press **Save map**. Saving
   over an existing name asks before replacing it.

   ![Invalid name](RaspberryPi_ROS2/images/gui-map-name-validation.png)

5. Press **Navigate with <name>**. The stack restarts in navigation on the new map.

   ![Map saved](RaspberryPi_ROS2/images/gui-map-saved.png)

While mapping, tasks are disabled: mapping has no navigation.

## 7. Manage saved maps

![Maps tab](RaspberryPi_ROS2/images/gui-maps-library.png)

The **Maps** tab lists every map with a thumbnail, size and age. Badges: **In use**, **Default**,
**Edited**, **No pose graph** (the robot cannot localize on it; map it again).

| Action | How |
|---|---|
| Use a map | **Navigate with <name>** on its row |
| Edit | `⋯` → **Edit** (section 8) |
| Rename / Duplicate | `⋯` → **Rename** / **Duplicate**, type the name, Enter (Esc cancels) |
| Make it the default | `⋯` → **Set as default** (used by System and by `navigation.launch.py` through the launcher) |
| Undo edits | `⋯` → **Revert edits** restores the original scan |
| Delete | `⋯` → **Delete**, confirm; press **Undo** on the toast to bring it back |

The map in use cannot be renamed or deleted. Racks, drop zones and lanes belong to the robot, not
to a map, so they stay when you switch maps.

## 8. Edit a map

![Map editor](RaspberryPi_ROS2/images/gui-map-editor.png)

Use the editor to block no-go areas, close gaps in walls or erase scan noise.

1. **Maps → ⋯ → Edit**.
2. Pick **Brush** or **Line**, and what to paint: **Wall**, **Free** or **Unknown**.
3. Set the size with the slider or `[` / `]`. Drag on the map to paint (Line: drag from start to
   end).
4. `Ctrl+Z` / `Ctrl+Shift+Z` undo and redo.
5. **Save**. **Close** asks before discarding unsaved edits.

Edits are used by the global planner the next time navigation starts on that map. Localization
still uses the original scan. **Revert edits** brings the original image back at any time.

## 9. Set up the dock, racks and drop zones

![Locations](RaspberryPi_ROS2/images/gui-locations-panel.png)

1. Open **Locations**.
2. **+ Rack** or **+ Drop zone**: type a name and coordinates, or press **Pick on map** and then
   click and drag on the map: the click sets the position, the drag sets the heading (the side the
   robot enters from).
3. Edit the **Home Dock** with the pencil the same way.
4. Set each rack **Occupied** or **Free** with its state button, or right-click it on the map.
5. **Show on map** (crosshair) centres the map on a location; the bin deletes it (click again to
   confirm).

Locations are saved on the robot in `~/.ros/amr_locations.json` and survive rebuilds.

## 10. Draw lanes

Lanes are the corridors the robot prefers. Corners and junctions are rounded automatically, so
there are never sharp points.

1. **Locations → Lane**.
2. Click points along the corridor. **Undo** removes the last point.
3. **Finish** (or **Cancel**). Ends that land near another lane snap onto it and become a
   junction; crossings get a shared junction point.
4. The lane button on the map toolbar (**Show lanes** / **Hide lanes**) toggles all lanes.

![Rounded lanes and junctions](RaspberryPi_ROS2/images/gui-lanes-rounded.png)

With lanes drawn, the robot drives along them as one smooth curve. Change the rounding with
`lane_corner_radius` in `amr_mission_control/config/mission_manager.yaml`.

## 11. Plan and run missions

1. **Tasks → Move rack**: choose **From** (only occupied racks are offered) and **To** (only free
   racks and drop zones), then **Add to queue**.
   **Go to** sends the robot to any location.
2. Or **double-click the map** to send the robot straight to that point.
3. Reorder the queue by dragging the handle; remove a task with its × and confirm.
4. **Start mission** runs the queue from the top. The Robot panel shows each step (Drive to, Align
   under rack, Lift rack, Carry to, Insert into slot, Lower rack) and a progress bar.

   ![Task progress](RaspberryPi_ROS2/images/gui-task-progress.png)

5. **Pause** / **Resume**, **Stop** (cancels the queue), **Return to dock**.

Rack states update automatically when a transport finishes.

## 12. Drive by hand, lift and speed

![Manual drive](RaspberryPi_ROS2/images/gui-manual-drive.png)

- Hold `W` / `S` to drive forward / back, `A` / `D` to turn, from anywhere in the app (not while
  typing or in a dialog), or hold the pad buttons with mouse or touch. Releasing stops the robot.
- **Lift up** / **Lift down** raise and lower the rack lift.
- **Max speed** limits autonomous driving (0.10–1.00 m/s).

## 13. Layers: costmaps, lidars, obstacles

![Layers](RaspberryPi_ROS2/images/gui-layers-menu.png)

Open **Layers** (stack icon on the map toolbar) and switch what you need:

- **Map**: occupancy map, metre grid.
- **Navigation**: global costmap, local costmap, planned path, footprint.
- **Sensors**: merged / front / rear lidar, dynamic obstacles (red boxes around things that are
  not on the map), rack legs.
- **Mission**: lanes, racks/zones/dock, robot trail.

A layer only downloads data while it is on. Your choices are remembered in this browser. The
legend at the bottom lists what is currently visible.

![Costmap layer](RaspberryPi_ROS2/images/gui-global-costmap.png)

## 14. Emergency stop

- **E-stop** (red, top right) cancels everything, stops the wheels and, on the real robot,
  latches the hardware e-stop (the hoverboard brakes and its power relay opens).
- To continue: release the physical button if pressed, then **Release E-stop** in the top bar and
  confirm. The release is refused while the physical button is still pressed.

## 15. Simulation (Gazebo)

On a PC installed with `--sim`, choose **Simulation** in System, or add `sim:=true` to any launch
file. The same GUI drives the simulated robot; RViz opens on the PC.

## 16. Keyboard shortcuts

| Where | Keys | Action |
|---|---|---|
| Anywhere | `W` `A` `S` `D` (hold) | Drive / turn |
| Map | Arrow keys | Pan |
| Map | `+` / `-` / `0` | Zoom in / out / fit |
| Map | Double-click | Go to point |
| Map | Right-click a rack | Flip free / occupied |
| Map editor | `B` / `L` | Brush / line |
| Map editor | `1` / `2` / `3` | Wall / free / unknown |
| Map editor | `[` / `]` | Smaller / larger brush |
| Map editor | `Ctrl+Z`, `Ctrl+Shift+Z` or `Ctrl+Y` | Undo, redo |
| Dialogs and name fields | `Enter` / `Esc` | Confirm / cancel |

## 17. Troubleshooting

| Symptom | Fix |
|---|---|
| "Robot not connected" | rosbridge is not running: launch from **System**, or start `mission_control.launch.py`. Check the robot IP and that port 9090 is reachable. |
| System shows "Launcher offline" | Start `launcher_server.py` on the robot (section 3); port 8088 must be reachable. |
| Map stays grey after launching navigation | The robot has not localized yet; wait a few seconds, or check the pose graph exists (no **No pose graph** badge). |
| Robot ignores painted walls | Edits apply the next time navigation starts on that map; Restart from System. `use_edited_map:=false` disables them. |
| RViz does not open | RViz needs a display on the robot computer; run it on a laptop instead. |
| Layers show nothing | The topic is not published in the current mode (for example costmaps while mapping). |
| GUI changes not visible after an update | Hard-refresh the browser; for a production build run `npm run build` in the GUI folder. |

More: [docs/troubleshooting.md](RaspberryPi_ROS2/docs/troubleshooting.md).

## 18. Upgrading from the old AGV names

The project was renamed from AGV to **AMR**: packages are now `amr_*`
(`amr_navigation`, `amr_mission_control`, …), the launcher variables are `AMR_MAPS_DIR` and
`AMR_LAUNCHER_PORT`, and robot files are `~/.ros/amr_*`. On every machine that built the old
workspace:

```bash
cd RaspberryPi_ROS2
rm -rf build install log          # old agv_* packages would otherwise linger
colcon build --symlink-install
source install/setup.bash
```

Nothing is lost: on first start the mission manager copies `~/.ros/agv_locations.json` to
`~/.ros/amr_locations.json`, and the launcher moves `~/.ros/agv_launcher.json` and
`~/.ros/agv_map_trash`. The old `AGV_MAPS_DIR` / `AGV_LAUNCHER_PORT` variables are still honoured.
Update any scripts or aliases that call `ros2 launch agv_…`.

## 19. Run a fleet (several robots, one laptop)

The laptop manages the fleet; robots never talk to each other directly. Every robot runs the
**same copy** of this repository; what makes a robot itself lives in `~/.ros/amr_robot.json`.
Technical reference: [RaspberryPi_ROS2/docs/fleet.md](RaspberryPi_ROS2/docs/fleet.md).

![Fleet view](RaspberryPi_ROS2/images/gui-fleet-3d.jpg)

**Try it first.** Open the GUI with `?preview` and stay on **Fleet**: three simulated robots work
the Hang1 layout on a one-way loop, a fourth turns up on the network waiting for setup, and the
traffic rules are the ones `fleet_server.py` runs.

### 19.1 Start the fleet manager (laptop)

```bash
pip install paramiko                                  # once: needed to set up Pis over SSH
python3 src/amr_mission_control/fleet_server.py       # http://<laptop>:8090
cd src/amr_mission_control/gui && npm run dev         # open the Fleet workspace
```

The laptop needs Python 3 and this repository; it does not need ROS.

### 19.2 Set up a new robot from the GUI

1. Flash **Ubuntu Server 24.04 (64-bit)** on the Pi's SD card, boot it on the same network and
   note its IP address (or its default hostname).
2. **Fleet › Set up a new Raspberry Pi.** Enter the address, user (`ubuntu`) and password, press
   **Connect**. The sheet shows what is on the Pi.
3. Check the suggested **Robot ID** (`AMR-04`); the **hostname** follows it (`amr-04`, reachable as
   `amr-04.local`). Optionally a name, the ROS domain (one per robot) and a **new password**.
4. Press **Set up AMR-04**. The laptop names the Pi, installs ROS 2 Jazzy, uploads and builds this
   workspace, runs `install_dependencies.sh`, sets the Robot ID, installs the launcher as a service
   that starts at boot and adds the robot to the fleet. A first setup takes 30–60 minutes; you can
   close the sheet, the job keeps running (reopen with **Show the Pi setup**).
5. Send it the fleet's map (**Send Hang1** on its card) and **Start navigation**.

**Deploy code** in a robot's panel uploads your latest workspace to it, builds and restarts its
launcher (stop its stack first). No password needed: the setup installed the laptop's key.

Already have a robot running `launcher_server.py`? It shows under **Found on the network** within a
second; **Set up** gives it its Robot ID. If broadcasts are blocked, use **Add it by address**.

### 19.3 Lanes, racks and their faces (Layout tab)

![Layout editing](RaspberryPi_ROS2/images/gui-fleet-layout.jpg)

**Draw lane** (the view switches to Plan): click to place points. Near an existing node the
cursor snaps onto it, and clicking there **links** the new lane to it; clicking the lane's own
**first point closes the loop**. Each end can be **Connect** (joins the lane it touches) or **Open**
(keeps an open end, drawn as a stop bar). **Enter** finishes, **Esc** cancels.

**Edit nodes** shows every lane node: drag one to move it (every lane through it follows), drop it
on another node to **link** them, select one and press **Delete** to remove it. Gold rings are open
ends. The arrow button on a lane makes it **one-way** (driven only in the order it was drawn;
arrows on the floor); the swap button reverses it. Use one-way lanes for loops: robots then only
follow each other and never meet head-on.

**Racks and drop zones**: every site has a **face**, the arrow from the site to the lane. Where the
arrow meets the lane is the **only** point a robot enters from, straight in and at 90° to the lane.
**Add rack** / **Add drop zone**, then click the floor: the site is placed and aimed square at the
nearest lane. Each row says which lane it faces; **Face the next lane** turns it to another lane,
**Move** re-places it, the name is edited in place. A site that isn't square to a lane is flagged.

### 19.4 How robots drive and keep apart

**Lanes only.** Once lanes exist, a robot never plans a free path during a task: it backs straight
out of a rack slot or its dock along that site's axis to the lane, follows the lanes to the
target's entry point, squares up and drives straight in. Place docks so their face meets a lane.

**Keeping apart.** Every driving robot reserves the next few metres of its path (the light strip
on the floor). Where two reservations overlap, one robot holds in place and continues the same
task once the way is clear:

- a robot standing in the other's path goes first (a leader before its follower);
- otherwise **Traffic priority**, then a robot carrying a rack, then whoever started first;
- head-on, the robot with the shorter way out **backs off** along the lane (or back up its rack's
  spur) and waits; if the lane can't clear it, it takes a **refuge**: under an occupied rack when
  empty, only into a free location when carrying;
- a circle of holds lets one robot go; two robots that still block each other are reported so you
  can drive one aside; a robot stuck behind a parked one gets a **Dock** button for the parked one.

Each robot's costmaps also mark the other robots (`/fleet/peers`), so Nav2 sees robots around
corners its lidar can't.

> Safety: robots under fleet control need the laptop's heartbeat to drive. If the fleet manager
> stops, they hold position (docking and lifting are not interrupted). To run a robot on its own
> again, remove it from the fleet, or send `{"action": "fleet_detach"}` from its console.

### 19.5 Maps

Robots share coordinates only when they localize on the same pose graph. **Maps** keeps the
laptop's master copy of each map and shows each robot as *same copy*, *different* or *missing*;
copy a robot's map to the library, then send it to the others. A robot running a map can't have it
replaced until its stack stops. The **pencil** on the floor edits the fleet's map **live**: paint
walls or free floor, **Save**, and every robot with that copy gets it, robots navigating on it
reload it immediately.

### 19.6 Each robot

**Robot tab**: Robot ID, name, max speed, traffic priority, ROS domain and notes, saved on the robot
itself. **Console** (or the **Robot ▾** menu in the top bar) opens that robot's full mission control;
its top bar shows the fleet's map and whether this robot has the same copy and layout. **Dispatch**
sends a transport to the best free robot and says why it chose it.

**What runs where.** Preview simulates everything in the browser; on the real system the same rules
run in `fleet_server.py` (laptop) and `mission_manager_node.py` (each robot). Back-offs reverse the
Nav2 controller along the lane (MPPI `vx_min` -0.3); check that on the real robot before trusting it
in a narrow aisle.
