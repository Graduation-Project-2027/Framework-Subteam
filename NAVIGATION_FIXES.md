# Navigation Package Fixes — Action Log

## Overview

Stabilized the AGV navigation stack for ROS 2 Jazzy, resolving critical startup failures, controller crashes, and visualization issues.

---

## Fix 1 — MPPI "Omni" Model Case Sensitivity (CRITICAL)
**Problem:** MPPI controller failed to load with error `Model omni is not valid`. 
**Resolution:** Updated `motion_model` value to exactly `"Omni"` (capital O) in YAML configs. Jazzy is strictly case-sensitive for these model strings.

## Fix 2 — Collision Monitor Segfault (CRITICAL)
**Problem:** `collision_monitor` crashed with `InvalidParameterValueException` because `polygons` or `sources` arrays were empty or missing types in Jazzy.
**Resolution:** Reconfigured the node to use a pass-through structure where all plugins are present but explicitly set to `enabled: False`.

## Fix 3 — Inflation Radius vs Footprint (HIGH)
**Problem:** Nav2 threw `computeCircumscribedCost` errors because `inflation_radius` (0.35m) was smaller than the robot's circumscribed radius (0.517m).
**Resolution:** Increased `inflation_radius` to `0.55m` to satisfy the constraint and ensure efficient planning.

## Fix 4 — Invisible Costmaps (UI/UX)
**Problem:** RViz2 reported GLSL shader errors `active samplers with a different type refer to the same texture image unit` on Nvidia drivers when using the `costmap` color scheme.
**Resolution:** Changed default color scheme to `map` (grayscale) in `navigation.rviz`. (User subsequently reverted this to `costmap` for aesthetics, but the fix is documented as a fallback).

## Fix 5 — Spawn Pose Synchronization (HIGH)
**Problem:** Robot spawned at world origin but map was offset, causing localization to drift or fail initially.
**Resolution:** Synchronized Gazebo spawn coordinates and `slam_toolbox` `map_start_pose` to match user-specified coordinates: `(-1.545, 1.621)`.
