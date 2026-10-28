/*
 * Reboot into the STM32 system bootloader (USB DFU) without touching BOOT0.
 *
 * rebootToDfu() stores a flag in an RTC backup register (kept across resets) and resets the MCU.
 * The core's weak init() is overridden so the flag is checked before clocks, USB or the watchdog
 * start; the jump then happens from a clean reset state.
 */
#pragma once

void rebootToDfu();
