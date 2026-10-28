# Real robot hardware (Raspberry Pi 5)

| Component | Connection | ROS driver | ROS interface |
|---|---|---|---|
| 2x RPLidar A1M8 (front, rear) | USB serial, 115200 baud | `rplidar_ros/rplidar_node` (`/front_lidar/rplidar`, `/rear_lidar/rplidar`) | `/scan`, `/scan_rear` |
| MPU9250 (+ AK8963 compass) | Pi I2C bus 1, address 0x68 | `amr_hardware/mpu9250_driver` + `imu_filter_madgwick` | `/imu/data_raw`, `/imu/mag`, `/imu/temperature` → `/imu/data` |
| 1x hoverboard mainboard: two hub motors on the centre axle (differential drive, 4 corner casters) | CAN, SocketCAN `can0` | `amr_hardware/can_bridge` | `/cmd_vel` in; `/odom`, `/joint_states`, `/hoverboard/status` out |
| STM32F411CEU6 body controller: e-stop authority, relay feeding the hoverboard board | same CAN bus | `amr_hardware/can_bridge` | `/estop` service, `/estop/status` |
| Lift, lights, buzzer (planned) | same STM32F411 | to be added to `can_bridge` | `/lift_joint/cmd_pos` (already used by the mission manager and TUI) |

All drivers are started by `amr_bringup/bringup.launch.py` (and therefore by mapping and navigation)
through `amr_hardware/launch/hardware.launch.py`. Every tunable lives in
`src/amr_hardware/config/hardware.yaml`.

## 1. Operating system

1. Flash **Ubuntu 24.04 LTS Server (64-bit)** for Raspberry Pi and install **ROS 2 Jazzy**
   (`ros-jazzy-ros-base` is enough; no desktop needed).
2. Clone the repository and install the dependencies (no Gazebo/RViz on the robot):

   ```bash
   cd RaspberryPi_ROS2
   bash install_dependencies.sh
   ```

   The script also adds your user to `dialout` (lidars) and `i2c` (IMU). Log out and back in.
3. Build: `colcon build --symlink-install && source install/setup.bash`.

## 2. CAN bus

### Interface on the Pi

Any SocketCAN interface works:

- **USB adapter** (candleLight / gs_usb firmware): shows up as `can0` with no extra setup.
- **SPI HAT with an MCP2515/MCP2518FD**: enable its overlay in `/boot/firmware/config.txt`, using
  the crystal frequency and interrupt GPIO from the HAT's documentation, e.g.

  ```ini
  dtparam=spi=on
  dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
  ```

The hoverboard firmware runs at **1 Mbit/s** (`CAN_BITRATE` in `Hoverboard_Firmware/Inc/config.h`).
An MCP2515 with an 8 MHz crystal cannot reach 1 Mbit/s: use 500 kbit/s on every node instead.

Bring the interface up (test):

```bash
sudo ip link set can0 up type can bitrate 1000000 restart-ms 100
candump can0          # from can-utils
```

Make it permanent with systemd-networkd, `/etc/systemd/network/80-can0.network`:

```ini
[Match]
Name=can0

[CAN]
BitRate=1M
RestartSec=100ms
```

```bash
sudo systemctl enable --now systemd-networkd
```

Wiring: CAN_H/CAN_L twisted pair, shared GND, **120 Ω termination only at the two physical ends**
of the bus (remove the resistor on middle nodes).

### Hoverboard mainboard

The robot is a **differential drive**: one hoverboard mainboard flashed with `VARIANT_CAN` (see
[Hoverboard_Firmware](../../Hoverboard_Firmware/README.md)) drives the two hub motors on the centre
axle; four passive casters at the corners carry the rest of the load.

| Board | `CAN_NODE_ID` | Left motor | Right motor | Frames it sends |
|---|---|---|---|---|
| drive | `1` | left wheel | right wheel | 0x181 odom, 0x281 motion, 0x381 status |

Change `board_id` in `hardware.yaml` if you flash another ID. The firmware convention is
**positive = robot forward** for both wheels; if a wheel turns the wrong way, set
`INVERT_L_DIRECTION` / `INVERT_R_DIRECTION` in `config.h` (this flips the command and the feedback
together). Disable `DEBUG_SERIAL_USART3` in production builds (it adds loop jitter).

The board is powered through the e-stop relay. **Bypass its power latch** (the push-button / latch
circuit of the original hoverboard) so it boots by itself when the relay closes.

### E-stop controller (STM32F411CEU6, `BODY` node)

Firmware, wiring and the build/upload tool:
[STM32_CANbus_Embedded/Blackpill_CAN_Software](../../STM32_CANbus_Embedded/Blackpill_CAN_Software/README.md)
(Blackpill + MCP2515; an 8 MHz MCP2515 crystal limits the whole bus to 500 kbit/s).

The STM32F411 is the e-stop authority. It switches the relay that powers the hoverboard board and
has the physical e-stop button. Its required behaviour (documented in the DBC):

1. **Trip** on any of: `ESTOP = 1` from the Pi, its button, or no `HB_CMD` frame (0x201) for
   **500 ms** (Pi / bridge crashed; the bridge sends `HB_CMD` at 50 Hz, also when stopped). When the
   button trips it, it broadcasts `ESTOP = 1` itself.
2. **Brake, then cut power**: the hoverboard brakes actively on `ESTOP = 1`. Only once the wheels
   are stopped (or after a short timeout) does the controller open the relay, because an unpowered
   board lets the motors coast.
3. **Release** only on `ESTOP = 0` from the Pi, and **never while the button is pressed**. It then
   closes the relay; the hoverboard reboots and its tick counters restart at 0 (the bridge
   re-baselines its odometry).
4. Report its state in `SAFETY_STATUS` (0x011) at 10 Hz and immediately on change.

Protocol (from [hoverboard_can.dbc](../../Hoverboard_Firmware/docs/hoverboard_can.dbc), little-endian, n = hoverboard node id):

| ID | Direction | Content | Rate |
|---|---|---|---|
| 0x010 | Pi (or BODY button) → all | e-stop: `u8` 1 = latch, 0 = release | on request |
| 0x011 | BODY → Pi | byte 0: bit0 e-stop active, bit1 hoverboard power (relay closed), bit2 button pressed, bits 4-6 cause (0 none, 1 host request, 2 button, 3 host timeout); byte 1: heartbeat | 10 Hz + on change |
| 0x200+n | Pi → board | `i16` left rpm, `i16` right rpm | 50 Hz (bridge) |
| 0x180+n | board → Pi | `i32` left/right hall ticks (~90 per wheel revolution) | 50 Hz |
| 0x280+n | board → Pi | `i16` left/right rpm, `i16` left/right current [A×100] | 50 Hz |
| 0x380+n | board → Pi | battery V×100, battery %, temperature, error codes, status flags, CAN error counter, heartbeat | 10 Hz |

Lift, lights and buzzer frames for the same node will be added later (see section 5).

### What the CAN bridge does

- Converts `/cmd_vel` (`linear.x`, `angular.z`; `linear.y` is ignored) to left/right wheel speeds
  and sends them at 50 Hz. Without a `/cmd_vel` message for `cmd_vel_timeout` (0.5 s) it commands
  0 rpm. Both wheels are scaled together to stay under `max_wheel_rpm` (150 rpm ≈ 1.3 m/s) so the
  curvature is kept.
- While an e-stop is requested or `SAFETY_STATUS` reports it active, it commands 0 rpm, so a release
  never resumes an old command.
- Integrates the hall ticks into `/odom` (pose) and reports the measured wheel rpm as the twist
  (the EKF fuses only the twist). It publishes no TF: the EKF owns `odom → base_footprint`.
  Tick jumps (board restarted) are dropped and the odometry re-baselined.
- Publishes `/joint_states` for `left_wheel_joint` and `right_wheel_joint` so the wheels turn in RViz.
- Publishes every decoded hoverboard field on `/hoverboard/status`
  (`amr_interfaces/msg/HoverboardStatus`) and the e-stop controller state on `/estop/status`
  (`amr_interfaces/msg/SafetyStatus`) at 10 Hz, with `online: false` when a node stops reporting.
  E-stop changes and their cause, an offline board (unless the relay is open), hall/stall errors,
  low battery and over-temperature are also logged.
- Serves `/estop` (`std_srvs/srv/SetBool`): `true` latches the e-stop on every CAN node, `false`
  requests the release (refused by the controller while the button is pressed).

```bash
ros2 service call /estop std_srvs/srv/SetBool "{data: true}"    # stop everything
ros2 service call /estop std_srvs/srv/SetBool "{data: false}"   # release
ros2 topic echo /estop/status                                   # who stopped the robot and why
```

Without the STM32F411 on the bus the bridge warns every 30 s and the hoverboard's own e-stop
(active braking, no power cut) is the only one.

### Parameters to measure

`wheel_radius` and `wheel_separation` (distance between the two drive-wheel contact points) are
placeholders until the final CAD/URDF exists and **must be measured on the real robot**. Set them
in **both** `amr_hardware/config/hardware.yaml` and
`amr_description/urdf/urdf/drivetrain.urdf.xacro` (`drive_wheel_radius`, `drive_wheel_separation`;
the caster positions live there too). Check them by driving a known distance and a full turn and
comparing `/odom`: a wrong distance means a wrong radius, a wrong angle means a wrong separation.

## 3. Lidars (RPLidar A1M8)

- **Ports**: `ttyUSB0/1` depend on plug-in order. Use the stable paths from
  `ls -l /dev/serial/by-path/` as `serial_port` in `hardware.yaml`.
- **Mounting**: the URDF places the front lidar at (0.29, 0, 0.15) m from `base_link` and the rear
  one at (-0.29, 0, 0.15) m rotated 180°. Measure the real positions and update the two
  `lidar_sensor` blocks in `amr_description/urdf/urdf/robot.urdf.xacro`.
- **Orientation check**: open RViz on a laptop (fixed frame `base_link`) and put an object in front
  of the robot. If one lidar shows it behind the robot, set `flip_x_axis: true` for that lidar; if
  the scan is mirrored, set `inverted: true`.
- **Field of view**: the A1M8 sees 360°, but the half facing the robot body would hit the chassis and
  lift. `amr_mapping/config/laser_filters.yaml` crops both scans to ±125° (same as the simulated
  sensors). Adjust `lower_angle`/`upper_angle` if the real body blocks more or less.
- Motor control services: `/front_lidar/stop_motor`, `/front_lidar/start_motor` (same for `rear_lidar`).

## 4. IMU (MPU9250)

- **Wiring** (3.3 V part): VCC → pin 1 (3.3 V), GND → pin 6, SDA → pin 3, SCL → pin 5.
  `sudo i2cdetect -y 1` must show `68` (and `0c`, the magnetometer, once the driver has run).
- **Mounting**: x forward, y left, z up. Otherwise update the `imu_sensor` block (`xyz_offset`,
  `rpy_offset`) in `robot.urdf.xacro`.
- **Start-up**: keep the robot still for ~2 s while the gyro bias is measured. Re-measure at any time
  (robot still):

  ```bash
  ros2 service call /imu/calibrate_gyro std_srvs/srv/Trigger
  ```

- **Orientation**: `imu_filter_madgwick` turns `/imu/data_raw` into `/imu/data` (orientation +
  rates), which the EKF fuses. `publish_tf` is off: the EKF owns the odometry transform.
- **Compass**: `/imu/mag` is always published (in the accel/gyro axes, Tesla) but **not fused by
  default** (`use_mag: false`): the hub-motor magnets and steel racks distort it. To use it, do a
  hard-iron calibration first: rotate the robot a full turn while recording `/imu/mag`, set
  `mag_bias_x/y/z` in `hardware.yaml` to `(max + min) / 2` of each axis, then set `use_mag: true`.
- Boards that report `WHO_AM_I = 0x70` are MPU6500 clones without a magnetometer: the driver warns
  and runs without `/imu/mag`.

## 5. Lift, lights and buzzer (planned)

These will be driven by the same STM32F411CEU6 (`BODY` node) that controls the e-stop. When their
protocol is defined, add the frames to the DBC and handle them in `can_bridge.py` (one process owns
the bus):

- subscribe `/lift_joint/cmd_pos` (`std_msgs/Float64`, metres) — the mission manager and the TUI
  already publish it, and the Gazebo bridge consumes the same topic;
- publish the measured lift height as `lift_joint` in `/joint_states` (until then RViz shows the
  lift links without a transform on the real robot);
- expose lights/buzzer as topics or services.

Until the lift exists, missions run through their lift steps without moving a rack.

## 6. First-drive checklist

1. Wheels off the ground, e-stop within reach.
2. `candump can0` shows 0x181/0x281/0x381 from the hoverboard and 0x011 from the e-stop controller.
3. `ros2 launch amr_bringup bringup.launch.py` — no errors from `can_bridge`, `mpu9250`, both lidars.
4. `ros2 topic echo /hoverboard/status --once`: `online: true`, plausible battery.
   `ros2 topic echo /estop/status --once`: `online: true`, `hoverboard_power: true`.
5. E-stop test: press the button → wheels brake, then the relay opens (`hoverboard_power: false`,
   cause button). Release the button and call `/estop` with `false` → relay closes, board online
   again. Repeat with `/estop` `true`, and by killing the bridge (cause host timeout).
6. `ros2 topic hz /scan /scan_rear /imu/data /odom /odometry/filtered`.
7. Direction test, one at a time:

   ```bash
   ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.1}}"     # both wheels forward
   ros2 topic pub -r 10 /cmd_vel geometry_msgs/msg/Twist "{angular: {z: 0.3}}"    # left back, right forward
   ```

   On the floor, `angular.z > 0` must turn the robot **counter-clockwise** seen from above. If it
   turns clockwise, the left and right motors are swapped: swap their connectors (or their wiring).
8. Calibrate `wheel_radius`, `wheel_separation`, the lidar poses and the lidar orientation.
9. Map the site ([launch.md](launch.md#map-a-new-site)), then re-tune the docking parameters
   ([mission_control.md](mission_control.md#tuning-on-the-real-robot)).
