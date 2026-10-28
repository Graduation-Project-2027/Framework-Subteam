# Graduation Project 2027: Software Development

This directory contains the software developed for the graduation project AGV. It brings together the hoverboard motor-controller firmware and the Raspberry Pi ROS 2 robot framework.

## Projects

### Hoverboard firmware

[`Hoverboard_Firmware/`](Hoverboard_Firmware/) contains STM32 firmware for controlling the hoverboard motors using field-oriented control (FOC). The project includes a CAN variant for differential-drive AGV control, with per-wheel speed commands and motor/vehicle status reporting.

- Main documentation: [Hoverboard Firmware README](Hoverboard_Firmware/README.md)
- CAN message definitions: [hoverboard_can.dbc](Hoverboard_Firmware/docs/hoverboard_can.dbc)
- CAN settings and firmware configuration: [Inc/config.h](Hoverboard_Firmware/Inc/config.h)
- Default PlatformIO build variant: `VARIANT_CAN`

Build and upload using the PlatformIO extension or CLI from the firmware directory. The firmware's ST-Link upload configuration is defined in `platformio.ini`.

### Raspberry Pi ROS 2 framework

[`RaspberryPi_ROS2/`](RaspberryPi_ROS2/) contains the ROS 2 AGV framework. Its packages cover the robot description, simulation and bringup, mapping, localization, navigation, teleoperation, and web-based mission control.

- Main documentation: [ROS 2 README](RaspberryPi_ROS2/README.md)
- Detailed launch and troubleshooting guide: [guide.md](RaspberryPi_ROS2/guide.md)
- Current project handover: [Handover.md](RaspberryPi_ROS2/Handover.md)

The ROS 2 project documents ROS 2 Jazzy workflows. Its simulation and navigation quick start is:

```bash
cd RaspberryPi_ROS2
bash install_dependencies.sh
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 launch agv_navigation navigation_sim.launch.py mode:=2_lidars
```

Run the dependency installer once per machine. Source the ROS and workspace setup files in each new terminal before running ROS commands.

## Software relationship

The ROS 2 framework provides the robot-level simulation, navigation, and mission-control software. The hoverboard firmware provides low-level motor control and describes its CAN interface in the DBC file. The firmware and ROS 2 projects are maintained as separate subprojects; this overview does not imply that a physical ROS 2-to-CAN bridge has been implemented or verified. See each project's documentation for its current capabilities and hardware requirements.

## Requirements at a glance

- **ROS 2 framework:** ROS 2 Jazzy on a compatible Linux system; the project installer handles the listed ROS packages and GUI dependencies.
- **Hoverboard firmware:** PlatformIO with the STM32 platform and an ST-Link for uploading to the target board. The firmware repository also provides a Python/Tkinter configuration and build GUI.
