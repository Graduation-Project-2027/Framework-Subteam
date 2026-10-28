/*
 * AMR body controller (BODY node) - STM32F411CEU6 Blackpill + MCP2515.
 *
 * E-stop authority on the robot CAN bus: switches the relay that powers the hoverboard board, reads
 * the e-stop button, watches the Pi (HB_CMD) and reports SAFETY_STATUS (0x011). Behaviour and
 * protocol: RaspberryPi_ROS2/docs/hardware.md and Hoverboard_Firmware/docs/hoverboard_can.dbc.
 */
#include <Arduino.h>
#include <IWatchdog.h>
#include <SPI.h>

#include "app.h"
#include "config.h"
#include "console.h"
#include "protocol.h"

static_assert(MCP2515_CRYSTAL_HZ / (2 * CAN_BITRATE) >= 8,
              "MCP2515 crystal too slow for CAN_BITRATE (an 8 MHz crystal reaches at most 500 kbit/s)");

namespace app {
MCP2515 can(SPI, PIN_MCP_CS, MCP_SPI_HZ);
Safety safety({
    HOST_TIMEOUT_MS, BUTTON_DEBOUNCE_MS, BRAKE_STOP_RPM, BRAKE_STOP_HOLD_MS, BRAKE_TIMEOUT_MS, MOTION_TIMEOUT_MS,
    HB_STATUS_TIMEOUT_MS, ESTOP_RESEND_MS, WATCHDOG_ARM_ON_BOOT != 0, BOOT_LATCHED != 0,
});
Stats stats;
}  // namespace app

namespace {

bool canReady_ = false;
MCP2515::Result lastInit_ = MCP2515::OK;
uint32_t lastCanTry_ = 0, lastStatus_ = 0, lastHealth_ = 0;
uint8_t heartbeat_ = 0, lastEflg_ = 0;
bool statusDue_ = true;

bool readButton() { return digitalRead(PIN_ESTOP_BUTTON) == BUTTON_PRESSED_LEVEL; }

void writeRelay(bool closed) { digitalWrite(PIN_RELAY, closed == (RELAY_ACTIVE_HIGH != 0) ? HIGH : LOW); }

void writeLed(bool on) { digitalWrite(PIN_LED, on != (LED_ACTIVE_LOW != 0) ? HIGH : LOW); }

const char *initError(MCP2515::Result r) {
    switch (r) {
        case MCP2515::NO_RESPONSE:  return "no SPI response (check wiring, 5 V/3.3 V supply, CS PA4)";
        case MCP2515::BAD_BITRATE:  return "bitrate not reachable with this crystal";
        case MCP2515::MODE_TIMEOUT: return "did not enter normal mode";
        default:                    return "ok";
    }
}

void initCan() {
    lastCanTry_ = millis();
    MCP2515::Result r = app::can.begin(MCP2515_CRYSTAL_HZ, CAN_BITRATE);
    canReady_ = r == MCP2515::OK;
    if (canReady_) {
        const CanTiming &t = app::can.timing();
        console::printf("[%6lu.%03lu] CAN ready: %lu kbit/s, MCP2515 %lu MHz, %u TQ, sample point %u.%u %%\r\n",
                        (unsigned long)(lastCanTry_ / 1000), (unsigned long)(lastCanTry_ % 1000),
                        (unsigned long)(CAN_BITRATE / 1000), (unsigned long)(MCP2515_CRYSTAL_HZ / 1000000), t.tq,
                        t.samplePermille / 10, t.samplePermille % 10);
        lastEflg_ = 0;
        statusDue_ = true;
    } else {
        app::stats.canInitFail++;
        if (r != lastInit_) {
            char msg[100];
            snprintf(msg, sizeof msg, "CAN init failed: %s. Retrying every %u ms", initError(r), CAN_RETRY_MS);
            console::event(msg);
        }
    }
    lastInit_ = r;
}

void dispatch(const CanFrame &f, uint32_t now) {
    app::stats.rx++;
    console::onFrame(f, false);
    if (f.ext || f.rtr) return;
    const uint32_t id = f.id;
    if (id == proto::ID_ESTOP) {
        if (f.len >= 1) app::safety.onEstopFrame(f.data[0] & 0x01, now);
    } else if (id == proto::ID_HB_CMD_BASE + HOVERBOARD_NODE_ID) {
        app::safety.onHostCommand(now);
    } else if (id == proto::ID_HB_MOTION_BASE + HOVERBOARD_NODE_ID) {
        if (f.len >= 4) app::safety.onMotion(proto::le16(&f.data[0]), proto::le16(&f.data[2]), now);
    } else if (id == proto::ID_HB_STATUS_BASE + HOVERBOARD_NODE_ID) {
        if (f.len > proto::HB_STATUS_FLAGS_BYTE)
            app::safety.onHoverboardStatus(f.data[proto::HB_STATUS_FLAGS_BYTE] & proto::HB_STATUS_ESTOP_BIT, now);
    }
}

void pollCan(uint32_t now) {
    CanFrame f;
    for (int i = 0; i < 16 && app::can.receive(f); i++) dispatch(f, now);
}

void checkCanHealth(uint32_t now) {
    if (now - lastHealth_ < 100) return;
    lastHealth_ = now;
    if (!app::can.responding(MCP2515::NORMAL)) {   // module unplugged, reset or brown-out
        canReady_ = false;
        lastInit_ = MCP2515::OK;
        console::event("MCP2515 stopped responding; re-initialising");
        initCan();
        return;
    }
    uint8_t e = app::can.eflg();
    if (e & (MCP2515::EFLG_RX0OVR | MCP2515::EFLG_RX1OVR)) {
        app::stats.rxOverflow++;
        app::can.clearOverflow();
    }
    const uint8_t rising = e & ~lastEflg_;
    if (rising & MCP2515::EFLG_TXBO) {
        app::stats.busOff++;
        console::event("CAN bus-off (no other node acknowledges, or wrong bitrate). Recovering automatically");
    } else if (rising & MCP2515::EFLG_TXEP) {
        console::event("CAN transmit error-passive: check termination, wiring and bitrate");
    }
    if ((lastEflg_ & MCP2515::EFLG_TXBO) && !(e & MCP2515::EFLG_TXBO)) console::event("CAN bus-off recovered");
    lastEflg_ = e & ~(MCP2515::EFLG_RX0OVR | MCP2515::EFLG_RX1OVR);
}

void sendPending(uint32_t now) {
    if (!canReady_) return;
    const int8_t b = app::safety.pendingBroadcast();
    if (b >= 0 && app::sendEstop(b == 1)) app::safety.ackBroadcast();

    if (app::safety.takeChanged()) statusDue_ = true;
    if (statusDue_ || now - lastStatus_ >= STATUS_PERIOD_MS) {
        CanFrame f;
        f.id = proto::ID_SAFETY_STATUS;
        f.len = 2;
        f.data[0] = app::safety.statusByte0();
        f.data[1] = heartbeat_;
        if (app::sendFrame(f)) {
            heartbeat_++;
            lastStatus_ = now;
            statusDue_ = false;
        }
    }
}

void updateLed(uint32_t now) {
    bool on;
    if (!canReady_) on = (now / 50) % 2;                    // fast flicker: CAN controller missing
    else if (app::safety.active()) on = (now / 250) % 2;    // 2 Hz: e-stop latched
    else on = now % 1000 < 60;                              // short heartbeat: running
    writeLed(on);
}

}  // namespace

namespace app {

bool canReady() { return canReady_; }

bool sendFrame(const CanFrame &f) {
    if (!canReady_) return false;
    if (!can.send(f)) {
        stats.txFail++;
        return false;
    }
    stats.tx++;
    console::onFrame(f, true);
    return true;
}

bool sendEstop(bool latch) {
    CanFrame f;
    f.id = proto::ID_ESTOP;
    f.len = 1;
    f.data[0] = latch ? 1 : 0;
    return sendFrame(f);
}

bool canSelfTest(char *msg, size_t n) {
    if (!canReady_) {
        snprintf(msg, n, "MCP2515 not ready");
        return false;
    }
    const uint32_t now = millis();
    pollCan(now);   // keep frames already received
    if (!can.setMode(MCP2515::LOOPBACK)) {
        snprintf(msg, n, "could not enter loopback mode");
        return false;
    }
    CanFrame tx;
    tx.id = 0x7E5;
    tx.len = 8;
    memcpy(tx.data, "SELFTEST", 8);
    bool ok = false;
    if (can.send(tx)) {
        CanFrame rx;
        const uint32_t t0 = millis();
        while (millis() - t0 < 20) {
            if (can.receive(rx)) {
                ok = rx.id == tx.id && rx.len == tx.len && !memcmp(rx.data, tx.data, 8);
                break;
            }
        }
    }
    const bool back = can.setMode(MCP2515::NORMAL);
    if (!back) canReady_ = false;
    snprintf(msg, n, ok ? "frame looped back through the controller%s" : "no loopback frame received%s",
             back ? "" : "; could not return to normal mode");
    return ok && back;
}

}  // namespace app

void setup() {
    pinMode(PIN_RELAY, OUTPUT);
    writeRelay(false);
    pinMode(PIN_ESTOP_BUTTON, INPUT_PULLUP);
    pinMode(PIN_LED, OUTPUT);
    writeLed(false);

    console::begin();
    const bool watchdogReset = IWatchdog.isReset(true);
    delay(5);   // button pull-up settles

    app::safety.setEventHandler(console::event);
    app::safety.begin(millis(), readButton());
    writeRelay(app::safety.relayClosed());

    SPI.begin();
    initCan();

    IWatchdog.begin(IWDG_TIMEOUT_US);
    if (watchdogReset) console::event("Restarted by the MCU watchdog");
}

void loop() {
    const uint32_t now = millis();
    IWatchdog.reload();

    if (canReady_) {
        pollCan(now);
        checkCanHealth(now);
    } else if (now - lastCanTry_ >= CAN_RETRY_MS) {
        initCan();
    }

    app::safety.update(now, readButton());
    writeRelay(app::safety.relayClosed());
    sendPending(now);
    updateLed(now);
    console::poll(now);
}
