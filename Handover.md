# Framework-Subteam Handover Notes (Current State)

This file is an implementation handover for future engineers/agents working on this repository.
It is based on the current workspace state and the original private repo README (`Graduation-Project-2027/Framework-Subteam`).

## 1) What has been added/changed

- Added a Rich TUI teleop node for direct robot control + odom monitoring:
  - `src/agv_bringup/scripts/rich_teleop_tui.py`
  - Run with: `ros2 run agv_bringup rich_teleop_tui`
  - Keys: `W/A/S/D` translation, `Q/E` rotation, `U/I` lift
- Added launch/install/runtime plumbing for the TUI:
  - `src/agv_bringup/CMakeLists.txt`
  - `src/agv_bringup/package.xml`
- Added dual-lidar robot model (without replacing original robot):
  - `src/agv_description/urdf/urdf/2l_robot.urdf.xacro`
  - Original `robot.urdf.xacro` remains available and untouched for default mode
- Added mode-based launch behavior (`single` by default, `2l` optional):
  - `src/agv_bringup/launch/agv_gazebo.launch.py`
  - `src/agv_navigation/launch/spawn_robot.launch.py`
- Added 2-lidar bridge/filter/nav config set:
  - `src/agv_bringup/config/gz_bridge_2l.yaml`
  - `src/agv_navigation/config/gz_bridge_2l.yaml`
  - `src/agv_navigation/config/laser_filters_2l.yaml`
  - `src/agv_navigation/config/navigation_2l.yaml`
  - `src/agv_navigation/config/amcl_localization_2l.yaml`
  - `src/agv_navigation/config/slam_toolbox_mapping_2l.yaml`
  - plus launch updates in `navigation.launch.py`, `localization.launch.py`, `mapping.launch.py`
- Updated lift mechanism behavior in shared lift macro:
  - `src/agv_description/urdf/urdf/lift.urdf.xacro`
  - Soft/upper range reduced to `0.25` (from `0.5`)
  - Lift velocity reduced to `0.12` (from `0.2`)
  - Added high surface friction on lift link collision (`mu=10`, `mu2=10`)

## 2) Current functional status

### Working and tested

- **World/simulation setup:** working.
- **Robot motion configuration:** working (cmd_vel behavior is configured and functioning).
- **Rich TUI teleop:** working well for movement + lift control in one terminal.

### Not working / not tested yet

- **Mapping:** currently not working and not validated end-to-end.
- **Navigation (Nav2):** currently not working and not validated end-to-end.

## 3) Important mode behavior (do not break)

- Default mode should preserve existing/original flow:
  - `mode:=single` (implicit default)
- New experimental mode:
  - `mode:=2l`
- Keep original robot path stable while iterating on 2-lidar mode.

## 4) Launch commands to know

### Default/original behavior

```bash
ros2 launch agv_bringup agv_gazebo.launch.py
ros2 launch agv_navigation spawn_robot.launch.py
ros2 launch agv_navigation navigation.launch.py
```

### Dual-lidar behavior

```bash
ros2 launch agv_bringup agv_gazebo.launch.py mode:=2l
ros2 launch agv_navigation spawn_robot.launch.py mode:=2l
ros2 launch agv_navigation navigation.launch.py mode:=2l
```

### TUI teleop

```bash
ros2 run agv_bringup rich_teleop_tui
```

## 5) Validation already done in this workspace

- Python syntax checks passed for edited launch/TUI files (`py_compile`).
- YAML parse checks passed for newly added config files.
- Full package build/runtime validation for nav/mapping has **not** been completed in this pass.

## 6) Highest-priority next work

1. Fix mapping launch/runtime path and verify map generation from start to save.
2. Fix Nav2 bringup path and verify localization + goal navigation end-to-end.
3. Verify `mode:=single` and `mode:=2l` in clean environments to ensure no regressions.
4. Validate lift friction behavior with a rack payload and tune if needed.

## 7) Notes for future agents

- Prefer adding new behavior under `mode:=2l` unless explicitly asked to alter default behavior.
- Treat mapping/nav stack as unstable until proven with reproducible test runs.
- If changing bridge/topic names, update both launch and YAML files together.
