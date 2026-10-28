#include "safety.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

#include "protocol.h"

void Safety::emit(const char *fmt, ...) {
    if (!event_) return;
    char buf[120];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    event_(buf);
}

void Safety::setPower(bool on, const char *why) {
    if (power_ == on) return;
    power_ = on;
    changed_ = true;
    emit(on ? "Relay closed: hoverboard powered (%s)" : "Relay opened: hoverboard unpowered (%s)", why);
}

void Safety::begin(uint32_t now, bool buttonRaw) {
    buttonRaw_ = button_ = buttonRaw;
    rawSince_ = now;
    hostArmed_ = cfg_.armOnBoot;
    lastHost_ = now;
    if (button_) {
        active_ = true;
        cause_ = proto::CAUSE_BUTTON;
        broadcast_ = 1;
        emit("Start-up with the e-stop button pressed: latched, relay open");
    } else if (cfg_.bootLatched) {
        active_ = true;
        cause_ = proto::CAUSE_NONE;
        emit("Start-up latched (BOOT_LATCHED): waiting for a release from the Pi");
    } else {
        setPower(true, "start-up");
    }
    changed_ = true;
}

void Safety::trip(uint8_t cause, bool broadcast, uint32_t now) {
    if (broadcast) broadcast_ = 1;
    if (active_) return;   // keep the first cause
    active_ = true;
    cause_ = cause;
    brakeStart_ = now;
    still_ = false;
    changed_ = true;
    emit("E-STOP latched: %s", proto::causeName(cause));
}

bool Safety::release(uint32_t now) {
    if (button_) {
        emit("Release refused: e-stop button is pressed");
        changed_ = true;   // report promptly so the Pi sees why
        return false;
    }
    if (!active_) return true;
    active_ = false;
    still_ = false;
    // Re-arm the host watchdog on the next HB_CMD, so a release sent without the bridge running
    // (e.g. cansend) does not trip again immediately.
    hostArmed_ = false;
    lastHost_ = now;
    changed_ = true;
    emit("E-stop released");
    setPower(true, "release");
    return true;
}

void Safety::onEstopFrame(bool latch, uint32_t now) {
    if (latch) {
        trip(proto::CAUSE_HOST_REQUEST, false, now);
    } else {
        release(now);
    }
}

void Safety::onHostCommand(uint32_t now) {
    lastHost_ = now;
    hostSeen_ = true;
    if (!hostArmed_) {
        hostArmed_ = true;
        emit("Host link up: watchdog armed (%lu ms)", (unsigned long)cfg_.hostTimeoutMs);
    }
}

void Safety::onMotion(int16_t rpmLeft, int16_t rpmRight, uint32_t now) {
    rpmL_ = rpmLeft;
    rpmR_ = rpmRight;
    lastMotion_ = now;
    motionSeen_ = true;
}

void Safety::onHoverboardStatus(bool estopActive, uint32_t now) {
    hbEstop_ = estopActive;
    lastHb_ = now;
    hbSeen_ = true;
}

void Safety::update(uint32_t now, bool buttonRaw) {
    // Debounce: accept a new level once it has been stable for debounceMs.
    if (buttonRaw != buttonRaw_) {
        buttonRaw_ = buttonRaw;
        rawSince_ = now;
    }
    if (button_ != buttonRaw_ && now - rawSince_ >= cfg_.debounceMs) {
        button_ = buttonRaw_;
        changed_ = true;
        emit(button_ ? "E-stop button pressed" : "E-stop button released (the Pi must still release the e-stop)");
        if (button_) trip(proto::CAUSE_BUTTON, true, now);
    }

    if (hostArmed_ && now - lastHost_ > cfg_.hostTimeoutMs) {
        hostArmed_ = false;
        emit("Host link lost: no HB_CMD for %lu ms", (unsigned long)(now - lastHost_));
        trip(proto::CAUSE_HOST_TIMEOUT, true, now);
    }

    if (active_ && power_) {
        const bool fresh = motionFresh(now);
        const bool still = fresh && abs(rpmL_) <= (int)cfg_.stopRpm && abs(rpmR_) <= (int)cfg_.stopRpm;
        if (still && !still_) stillSince_ = now;
        still_ = still;
        const uint32_t braking = now - brakeStart_;
        if (still_ && now - stillSince_ >= cfg_.stopHoldMs) {
            setPower(false, "wheels stopped");
        } else if (!fresh && braking >= cfg_.motionTimeoutMs) {
            setPower(false, "no motion feedback from the hoverboard");
        } else if (braking >= cfg_.brakeTimeoutMs) {
            setPower(false, "brake timeout");
        }

        // The hoverboard must be latched while the relay is still closed.
        if (power_ && hbStatusFresh(now) && !hbEstop_ && now - lastResend_ >= cfg_.resendMs) {
            lastResend_ = now;
            broadcast_ = 1;
        }
    }
}

uint8_t Safety::statusByte0() const { return proto::statusByte0(active_, power_, button_, cause_); }
