/*
 * USB serial debug console (any baud rate). Type "help" for the commands.
 * Output never blocks the safety loop for long: if nobody reads the port, output is dropped.
 */
#pragma once

#include <stdint.h>

#include "MCP2515.h"

namespace console {
void begin();
void poll(uint32_t now);
void event(const char *msg);                    // timestamped event line
void onFrame(const CanFrame &f, bool tx);       // CAN trace when "log" is on
void printf(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
}  // namespace console
