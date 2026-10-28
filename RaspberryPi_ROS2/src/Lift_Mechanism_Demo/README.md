# Lift Mechanism Demo — real rig follows the Gazebo lift

Drives a physical 2-stepper lift so it mirrors the simulated lift in Gazebo.
The mechanism is **binary** — fully **LIFTED** or fully **DROPPED**, no middle.

## Signal chain

```
Gazebo SIM / Mission Control / Teleop TUI
        │  std_msgs/Float64  height in metres
        ▼   topic: /lift_joint/cmd_pos   (0.0 = down, 0.20 = up)
   micro-ROS agent  (UDP 8888, ROS_DOMAIN_ID = 0)
        │  Wi-Fi
        ▼
   ESP32  ── binarises height ──> 2 digital lines + 2 manual buttons
        │  GPIO32 = LIFT line, GPIO33 = DROP line  (active LOW)
        ▼
   Arduino UNO + GRBL/CNC shield
        │  blind-home → latched state machine → fixed-distance synced move
        ▼
   X & Y steppers (stepped together)  → real lift tracks the sim
```

## How it stays in sync with the SIM

The mission manager / TUI command the lift by publishing a height (metres) on
**`/lift_joint/cmd_pos`** (`std_msgs/Float64`) — the exact topic the Gazebo
bridge consumes (`gz_bridge.yaml`). The ESP32 subscribes to that **same** topic
and collapses the height to a binary intent: `height > 0.05 m → LIFT`, else
`DROP`. So whatever raises/lowers the lift in sim raises/lowers the real rig.

> The ESP **must** be on the same `ROS_DOMAIN_ID` as the sim. This project uses
> **domain 0** (see `~/.bashrc`), set in `MicroRosConfig.h`.

## Running against the sim

1. Start the sim/stack as usual (publishes on `/lift_joint/cmd_pos`).
2. Start the micro-ROS agent on the PC whose IP is in `MicroRosConfig.h`
   (`192.168.137.35`), on the SAME domain:
   ```bash
   ROS_DOMAIN_ID=0 ros2 run micro_ros_agent micro_ros_agent udp4 --port 8888
   ```
3. Power the ESP32. It joins Wi-Fi `Ejust`, connects to the agent, and starts
   following the sim. The two buttons override manually at any time.

Edit the Wi-Fi SSID/password and agent IP at the top of
`ESP32_Code/firmware/MicroRosConfig.h` for your network.

## Tuning the Arduino (`Stepper_firmware.ino`)

All knobs are grouped under `TUNE FOR YOUR RIG`:

| Constant            | Meaning                                                            |
|---------------------|-------------------------------------------------------------------|
| `STEPS_PER_MM`      | `(steps/rev × microstep) / mm-per-rev` of vertical travel         |
| `LIFT_DISTANCE_MM`  | Hard-coded real stroke (sim lifts 0.20 m; set to YOUR rig)         |
| `HOMING_TIME_MS`    | How long boot homing slams the motors DOWN before calling it zero |
| `STEP_DELAY_*_US`   | Ramp/cruise/homing speeds (smaller = faster)                      |
| `DIR_UP`, `INVERT_Y`| Flip if a motor turns the wrong way / the Y motor is mirrored      |

**Behaviour:** on boot the Arduino blind-homes (slams DOWN for
`HOMING_TIME_MS`, calls that zero = DROPPED). It then latches the commanded
state and, only on a *change*, drives BOTH motors `LIFT_DISTANCE_MM` in sync
with a trapezoidal ramp. Holding a command never re-triggers a move.
