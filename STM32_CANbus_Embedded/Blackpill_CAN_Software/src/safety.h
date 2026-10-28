/*
 * E-stop authority logic of the BODY node (no hardware access; time is passed in, in ms).
 *
 * Trip  : ESTOP=1 from the Pi, the e-stop button, or no HB_CMD for HOST_TIMEOUT_MS (once armed).
 *         A local trip (button, host timeout, console) is broadcast as ESTOP=1.
 * Brake : the relay stays closed while the hoverboard brakes; it opens once the wheels are stopped,
 *         when the board stops reporting motion, or after BRAKE_TIMEOUT_MS.
 * Release: only on ESTOP=0 and never while the button is pressed; the relay closes again.
 */
#pragma once

#include <stdint.h>

class Safety {
public:
    struct Config {
        uint32_t hostTimeoutMs, debounceMs, stopRpm, stopHoldMs, brakeTimeoutMs, motionTimeoutMs,
                 hbStatusTimeoutMs, resendMs;
        bool armOnBoot, bootLatched;
    };
    using EventFn = void (*)(const char *msg);

    explicit Safety(const Config &cfg) : cfg_(cfg) {}
    void setEventHandler(EventFn fn) { event_ = fn; }

    void begin(uint32_t now, bool buttonRaw);
    void update(uint32_t now, bool buttonRaw);

    // Bus inputs
    void onEstopFrame(bool latch, uint32_t now);
    void onHostCommand(uint32_t now);
    void onMotion(int16_t rpmLeft, int16_t rpmRight, uint32_t now);
    void onHoverboardStatus(bool estopActive, uint32_t now);

    // Console
    void trip(uint8_t cause, bool broadcast, uint32_t now);
    bool release(uint32_t now);

    // Outputs
    bool relayClosed() const { return power_; }
    bool active() const { return active_; }
    bool buttonPressed() const { return button_; }
    uint8_t cause() const { return cause_; }
    bool hostArmed() const { return hostArmed_; }
    uint32_t hostAge(uint32_t now) const { return hostSeen_ ? now - lastHost_ : UINT32_MAX; }
    bool motionFresh(uint32_t now) const { return motionSeen_ && now - lastMotion_ <= cfg_.motionTimeoutMs; }
    bool hbStatusFresh(uint32_t now) const { return hbSeen_ && now - lastHb_ <= cfg_.hbStatusTimeoutMs; }
    bool hbEstop() const { return hbEstop_; }
    int16_t rpmLeft() const { return rpmL_; }
    int16_t rpmRight() const { return rpmR_; }
    uint8_t statusByte0() const;

    bool takeChanged() { bool c = changed_; changed_ = false; return c; }
    // -1 nothing to send, 0 = broadcast ESTOP=0, 1 = broadcast ESTOP=1. Call ackBroadcast() once sent.
    int8_t pendingBroadcast() const { return broadcast_; }
    void ackBroadcast() { broadcast_ = -1; }

private:
    void setPower(bool on, const char *why);
    void emit(const char *fmt, ...);

    Config cfg_;
    EventFn event_ = nullptr;

    bool active_ = false, power_ = false, button_ = false, buttonRaw_ = false;
    uint8_t cause_ = 0;
    uint32_t rawSince_ = 0;

    bool hostArmed_ = false, hostSeen_ = false;
    uint32_t lastHost_ = 0;

    bool motionSeen_ = false;
    uint32_t lastMotion_ = 0;
    int16_t rpmL_ = 0, rpmR_ = 0;

    bool hbSeen_ = false, hbEstop_ = false;
    uint32_t lastHb_ = 0;

    uint32_t brakeStart_ = 0, stillSince_ = 0, lastResend_ = 0;
    bool still_ = false;

    bool changed_ = true;
    int8_t broadcast_ = -1;
};
