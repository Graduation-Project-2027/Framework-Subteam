# Changelog

All changes made to this firmware (based on [hoverboard-firmware-hack-FOC](https://github.com/EFeru/hoverboard-firmware-hack-FOC)) for the Graduation Project 2027 AMR.

---

## 2026-10-28 — AMR CAN protocol, configuration GUI

### Added
- **AMR CAN protocol in `VARIANT_CAN`** ([Src/can.c](Src/can.c), [Inc/can.h](Inc/can.h))
  - Per-wheel speed commands in **rpm** (`0x200 + node`), converted to the FOC speed target using `N_MOT_MAX`.
  - **E-stop broadcast** frame `0x010` (1 = stop, latched; 0 = release): active braking to 0 rpm, rate limiter and filter bypassed.
  - **Odometry frame** `0x180 + node` at 50 Hz: signed, wrapping int32 hall-step counters per wheel (6 per electrical revolution, ~90 per wheel revolution).
  - **Motion frame** `0x280 + node` at 50 Hz: wheel speed [rpm] and DC-link current per motor [A×100], averaged over the frame period.
  - **Status frame** `0x380 + node` at 10 Hz: battery voltage [V×100], battery % (Li-ion cell curve), board temperature [°C], error codes (left/right nibble), status bits (enabled, command timeout, safe state, e-stop, auxiliary input, battery low, temperature warning), CAN error counter (max of TEC/REC), heartbeat counter.
  - Same sign convention for commands and all feedback (positive = forward); `INVERT_L_DIRECTION` / `INVERT_R_DIRECTION` flip a wheel's command and feedback together.
  - Acceptance filter receives only the command and e-stop IDs.
- **Hall-sensor odometry and current accumulation** in the 16 kHz motor ISR ([Src/bldc.c](Src/bldc.c)): uses the same hall-to-position map and direction convention as the BLDC controller; ambiguous half-revolution jumps are skipped.
- **DBC file** [docs/hoverboard_can.dbc](docs/hoverboard_can.dbc) describing every frame (for `cantools`, SavvyCAN, the ROS 2 hardware interface, the STM32F411 code). Verified with `cantools` against the firmware byte layout.
- **Hoverboard Firmware Tool (GUI)** [tools/hoverboard_gui.py](tools/hoverboard_gui.py), launched with [Hoverboard Tool.bat](Hoverboard%20Tool.bat)
  - Variant selection; writes `default_envs` in `platformio.ini`.
  - `config.h` editor: section sidebar (general sections + the selected variant only), checkboxes for flags, drop-downs for enumerations (control type/mode, board variant, CAN pins, bitrate, baud rates, input types, input priority), text boxes for numbers/expressions, 5-field editor for `PRI_/AUX_INPUT` tuples, tooltips with the full comment, `[condition]` tags for options inside `#if` blocks, filter box, inline validation.
  - Save rewrites only changed lines (keeps alignment, comments and line endings), creates `Inc/config.h.bak` on the first save, warns if the file changed on disk.
  - Build, Upload (ST-Link), Clean, Full chip erase and Unlock (remove read-out protection) via PlatformIO / OpenOCD, with confirmation for destructive actions, Stop button and a colored live log.
  - Serial monitor (uses PlatformIO's bundled Python/pyserial, no extra installs).
- `Inc/config.h.bak` and `tools/__pycache__/` added to [.gitignore](.gitignore).
- README section "Hoverboard Firmware Tool (GUI)".

### Changed
- **`VARIANT_CAN` settings** ([Inc/config.h](Inc/config.h)): `MOTOR_LEFT_ENA` enabled (both motors), `CTRL_MOD_REQ = SPD_MODE`, `FILTER = 65535` (low-pass bypassed), `TANK_STEERING` (per-wheel commands), `INVERT_L/R_DIRECTION` options listed.
- **CAN settings**: default `CAN_BITRATE` 1 Mbit/s (matches the Pi / STM32F411 bus); new `CAN_ID_ESTOP`, `CAN_ID_ODOM`, `CAN_ID_MOTION`, `CAN_ID_STATUS`, `CAN_STATUS_PERIOD`; documentation of transceiver wiring (TJA1050, 5 V supply, termination) and clock tolerance.
- **Bit timing**: 16 time quanta, 75 % sample point, SJW 4 at every bitrate. Tolerates ~2 % clock deviation of the crystal-less hoverboard board against crystal-clocked nodes.
- **Main loop** ([Src/main.c](Src/main.c)): feedback is sent by `CAN_FeedbackTask()`; e-stop resets the command rate limiter and filter; no inactivity power-off while CAN commands are being received.
- **Timeout handling** ([Src/util.c](Src/util.c)): e-stop forces a zero command after the timeout logic.
- New config check: `SPD_MODE` / `TRQ_MODE` require `CTRL_TYP_SEL = FOC_CTRL`.
- README `VARIANT_CAN` description updated.

### Verified
- All 11 variants build. `VARIANT_CAN` also builds with `DUAL_INPUTS`, `CAN_PINS 0` and `VLT_MODE`; no new compiler warnings with `-Wall -Wextra`.
- GUI tested on copies of `config.h` / `platformio.ini`: no-op save is byte-identical, edits change only the intended lines, invalid input is rejected, variant switching, real build, Stop, OpenOCD script loading (without connecting to the target), COM port listing, monitor error handling.
- **Not yet tested on hardware** (CAN bus, odometry direction, e-stop, Upload/Erase/Unlock from the GUI).

---

## 2026-10-27 — CAN bus variant (first version)

### Added
- **`VARIANT_CAN`** build variant: [platformio.ini](platformio.ini) environment (now the default), entry in the variant list of [Inc/config.h](Inc/config.h), README entry, `Src/can.c` added to the [Makefile](Makefile).
- **Register-level bxCAN driver** (no HAL CAN module, so it works with both the bundled and PlatformIO's STM32Cube HAL): GPIO/remap setup, bit timing from PCLK1 = 32 MHz with a compile-time check of the bitrate, identifier-list filter into FIFO 0, RX interrupt at lower priority than the motor ISR, non-blocking transmit over 3 mailboxes, automatic bus-off recovery.
- **RX interrupt handler** `USB_LP_CAN1_RX0_IRQHandler` in [Src/stm32f1xx_it.c](Src/stm32f1xx_it.c).
- **CAN as an input source** in [Src/util.c](Src/util.c): `CONTROL_CAN` (priority 0 or 1, dual-input with ADC supported), `timeoutFlgCAN` with `CAN_TIMEOUT` (safe state and 3 low-pitch beeps, same as a serial timeout).
- **CAN SETTINGS** in `config.h`: `CAN_PINS` (1 = PB8/PB9 remap, 0 = PA11/PA12), `CAN_BITRATE`, `CAN_NODE_ID`, frame IDs, timeout and feedback period.
- Config checks: valid `CAN_PINS`, `CAN_PINS 1` not allowed with `BOARD_VARIANT 1` (PB9 is the power button there), `CAN_NODE_ID` 1..127, CAN control not combined with serial control/sideboard inputs.

### Changed
- [Src/setup.c](Src/setup.c): the unused charger-detect pin is not configured when CAN uses PA11/PA12.

### Superseded
- The first feedback layout (`0x181` speeds/battery/temperature, `0x281` commands/current/status at 500 kbit/s) was replaced on 2026-10-28 by the AMR protocol above.

---

## Design notes (from the project discussion)
- **Architecture:** Raspberry Pi 5 (ROS 2: Nav2, slam_toolbox, `diff_drive_controller`, `robot_localization`) as the single high-level controller; the hoverboard board and the STM32F411 (lift motors with encoders, homing, lights) are separate nodes on one CAN bus. The F411 has no CAN controller and needs an MCP2515 (or a G4/F446 MCU). The e-stop frame lets the F411 stop the drive directly.
- **Localization:** the hall-tick odometry and the MPU9250 yaw rate are combined in an EKF and corrected by lidar SLAM/AMCL. The MPU9250 magnetometer is not used indoors. A second IMU (sideboard MPU6050) does not remove drift and is not worth the integration effort; re-estimating the gyro bias whenever the robot is stopped helps more.
- **Battery %:** the voltage-based estimate sags under load; an INA226/INA228 on the main battery line gives a reliable value.

## Not done / open items
- Rename the project folder to "Hoverboard Firmware": blocked while VS Code has it open. Close VS Code, rename in Explorer, reopen (PlatformIO regenerates `.vscode`).
- `Src/can.c` is not added to the Keil project in `MDK-ARM`.
- Hardware bring-up: check wheel direction on a stand, `can_error_counter` at temperature, e-stop behavior, `TEMP_*` calibration (status uses the uncalibrated temperature).
- `DEBUG_SERIAL_USART3` is still enabled in `VARIANT_CAN` (blocking `printf` every 125 ms); disable it for production.
