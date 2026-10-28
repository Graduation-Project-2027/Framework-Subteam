/*
 * AMR body controller configuration (STM32F411CEU6 Blackpill + MCP2515).
 *
 * Pins follow the WeAct Blackpill v2/v3 pinout (../Docs/Blackpill_Pinout.jpg). PA11/PA12 are USB,
 * PA13/PA14 SWD, PC13 the blue LED, PA0 the KEY button.
 */
#pragma once

#include <Arduino.h>

#define FW_NAME    "AMR body controller"
#define FW_VERSION "0.1.0"

// ---------------------------------------------------------------- CAN bus (MCP2515 on SPI1)
// Set from platformio.ini build_flags (the Blackpill Tool edits them). Every node on the bus must
// use the same bitrate: hoverboard CAN_BITRATE (Hoverboard_Firmware/Inc/config.h) and the Pi
// (ip link ... bitrate). An 8 MHz MCP2515 crystal reaches at most 500 kbit/s.
#ifndef CAN_BITRATE
#define CAN_BITRATE        1000000      // [bit/s] 125000, 250000, 500000 or 1000000
#endif
#ifndef MCP2515_CRYSTAL_HZ
#define MCP2515_CRYSTAL_HZ 16000000     // [Hz] crystal on the MCP2515 module: 8, 16 or 20 MHz
#endif

#define PIN_MCP_CS         PA4          // SPI1: SCK PA5, MISO PA6, MOSI PA7
#define PIN_MCP_INT        PB0          // MCP2515 INT (active low). Set to -1 if not wired (polls instead)
#define MCP_SPI_HZ         8000000      // [Hz] MCP2515 maximum is 10 MHz

#define HOVERBOARD_NODE_ID 1            // CAN_NODE_ID of the hoverboard board (frames 0x200/0x280/0x380 + id)

// ---------------------------------------------------------------- I/O
#define PIN_RELAY          PB12         // relay that powers the hoverboard board
#define RELAY_ACTIVE_HIGH  1            // 1: HIGH closes the relay. Give the driver a pull-down so it stays open during reset
#define PIN_ESTOP_BUTTON   PB13         // e-stop button, normally closed to GND, internal pull-up
#define BUTTON_PRESSED_LEVEL HIGH       // NC contact: pressed (or a broken wire) reads HIGH
#define PIN_LED            PC13         // on-board blue LED
#define LED_ACTIVE_LOW     1

// ---------------------------------------------------------------- safety behaviour (RaspberryPi_ROS2/docs/hardware.md)
#define HOST_TIMEOUT_MS      500        // trip if no HB_CMD (0x200 + node) for this long (Pi / bridge crashed)
#define WATCHDOG_ARM_ON_BOOT 0          // 0: host watchdog arms on the first HB_CMD; 1: armed from power-up
#define BOOT_LATCHED         0          // 1: start with the e-stop latched (relay open) until the Pi releases it
#define BUTTON_DEBOUNCE_MS   20
#define BRAKE_STOP_RPM       5          // wheels count as stopped at or below this speed...
#define BRAKE_STOP_HOLD_MS   150        // ...for this long; then the relay opens
#define BRAKE_TIMEOUT_MS     1500       // open the relay at the latest after this long
#define MOTION_TIMEOUT_MS    200        // no motion frame for this long = board not reporting; open the relay
#define HB_STATUS_TIMEOUT_MS 300        // hoverboard status frame considered stale after this long
#define ESTOP_RESEND_MS      100        // re-send ESTOP=1 while the hoverboard still reports it is not latched
#define STATUS_PERIOD_MS     100        // SAFETY_STATUS (0x011) rate, plus immediately on change
#define CAN_RETRY_MS         1000       // retry MCP2515 initialisation after a failure

// ---------------------------------------------------------------- MCU watchdog
#define IWDG_TIMEOUT_US      250000     // a hung firmware resets the MCU; the relay output then floats (pull-down: open)

// ---------------------------------------------------------------- debug console (USB CDC, any baud rate)
#define STREAM_PERIOD_MS     200        // "@S" status lines for the Blackpill Tool after "stream on"
