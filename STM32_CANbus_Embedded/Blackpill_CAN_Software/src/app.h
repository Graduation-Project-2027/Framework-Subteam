/* Shared application objects (defined in main.cpp), used by the debug console. */
#pragma once

#include <stddef.h>

#include "MCP2515.h"
#include "safety.h"

struct Stats {
    uint32_t rx = 0, tx = 0, txFail = 0, rxOverflow = 0, busOff = 0, canInitFail = 0, consoleDrops = 0;
};

namespace app {
extern MCP2515 can;
extern Safety safety;
extern Stats stats;

bool canReady();
bool sendFrame(const CanFrame &f);
bool sendEstop(bool latch);
bool canSelfTest(char *msg, size_t n);   // MCP2515 loopback test; msg receives the result text
}  // namespace app
