// Host test of the e-stop logic (src/safety.cpp), no hardware needed. From the project folder:
//   g++ -std=c++17 -Iinclude -Isrc test_host/safety_host_test.cpp src/safety.cpp -o safety_test && ./safety_test
// (any host compiler: g++, clang++, or "python -m ziglang c++" after "pip install ziglang")
#include <cstdio>
#include <cstdlib>

#include "protocol.h"
#include "safety.h"

static int failures = 0;
#define CHECK(cond)                                                       \
    do {                                                                  \
        if (!(cond)) {                                                    \
            std::printf("  FAIL line %d: %s\n", __LINE__, #cond);         \
            failures++;                                                   \
        }                                                                 \
    } while (0)

static const Safety::Config CFG = {500, 20, 5, 150, 1500, 200, 300, 100, false, false};

static void quiet(const char *) {}

// Runs update() every ms from `from` to `to` (exclusive), feeding HB_CMD and motion at 50 Hz if asked.
static void run(Safety &s, uint32_t from, uint32_t to, bool button, bool host, int rpm, bool motion = true) {
    for (uint32_t t = from; t < to; t++) {
        if (host && t % 20 == 0) s.onHostCommand(t);
        if (motion && t % 20 == 0) s.onMotion(rpm, rpm, t);
        s.update(t, button);
    }
}

int main() {
    {
        std::puts("boot: relay closes, nothing latched");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        CHECK(s.relayClosed());
        CHECK(!s.active());
        CHECK(s.pendingBroadcast() == -1);
        CHECK(s.statusByte0() == 0x02);
    }
    {
        std::puts("boot with the button pressed: latched, relay open, ESTOP=1 broadcast");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, true);
        CHECK(!s.relayClosed());
        CHECK(s.active());
        CHECK(s.cause() == proto::CAUSE_BUTTON);
        CHECK(s.pendingBroadcast() == 1);
        CHECK(s.statusByte0() == (0x01 | 0x04 | (2 << 4)));
    }
    {
        std::puts("button: debounced, latches, brakes, then cuts power once the wheels stop");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        run(s, 0, 1000, false, true, 80);
        run(s, 1000, 1010, true, true, 80);     // 10 ms glitch: ignored
        run(s, 1010, 1100, false, true, 80);
        CHECK(!s.active());
        run(s, 1100, 1125, true, true, 80);     // held 25 ms: latched
        CHECK(s.active());
        CHECK(s.cause() == proto::CAUSE_BUTTON);
        CHECK(s.pendingBroadcast() == 1);
        s.ackBroadcast();
        CHECK(s.relayClosed());                 // still braking with power
        run(s, 1125, 1400, true, true, 40);     // wheels still turning
        CHECK(s.relayClosed());
        run(s, 1400, 1540, true, true, 0);      // stopped, but not for 150 ms yet
        CHECK(s.relayClosed());
        run(s, 1540, 1600, true, true, 0);
        CHECK(!s.relayClosed());
        std::puts("release refused while the button is pressed");
        CHECK(!s.release(1600));
        s.onEstopFrame(false, 1601);
        CHECK(s.active());
        std::puts("button let go: still latched until the Pi releases");
        run(s, 1601, 1700, false, true, 0, false);
        CHECK(s.active());
        CHECK(!s.buttonPressed());
        s.onEstopFrame(false, 1700);
        CHECK(!s.active());
        CHECK(s.relayClosed());
        CHECK(s.cause() == proto::CAUSE_BUTTON);  // last cause kept
    }
    {
        std::puts("brake timeout opens the relay even if the wheels keep turning");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        s.onEstopFrame(true, 100);
        CHECK(s.cause() == proto::CAUSE_HOST_REQUEST);
        CHECK(s.pendingBroadcast() == -1);      // the Pi already broadcast it
        run(s, 100, 1599, false, true, 60);
        CHECK(s.relayClosed());
        run(s, 1599, 1601, false, true, 60);
        CHECK(!s.relayClosed());
    }
    {
        std::puts("no motion feedback: relay opens after MOTION_TIMEOUT_MS");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        s.onEstopFrame(true, 100);
        run(s, 100, 299, false, true, 0, false);
        CHECK(s.relayClosed());
        run(s, 299, 301, false, true, 0, false);
        CHECK(!s.relayClosed());
    }
    {
        std::puts("host watchdog: arms on the first HB_CMD, trips after 500 ms of silence, broadcasts");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        run(s, 0, 2000, false, false, 0);       // never armed: no trip
        CHECK(!s.active());
        CHECK(!s.hostArmed());
        run(s, 2000, 3000, false, true, 0);
        CHECK(s.hostArmed());
        run(s, 3000, 3480, false, false, 0);    // last HB_CMD at 2980
        CHECK(!s.active());
        run(s, 3480, 3482, false, false, 0);
        CHECK(s.active());
        CHECK(s.cause() == proto::CAUSE_HOST_TIMEOUT);
        CHECK(s.pendingBroadcast() == 1);
        s.ackBroadcast();
        std::puts("host back: stays latched until released; a release without HB_CMD does not re-trip");
        run(s, 3482, 4000, false, true, 0);
        CHECK(s.active());
        s.onEstopFrame(false, 4000);
        CHECK(!s.active());
        run(s, 4001, 6000, false, false, 0);
        CHECK(!s.active());
    }
    {
        std::puts("re-send ESTOP=1 while the hoverboard reports it is not latched, stop once it is");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        run(s, 0, 100, false, true, 50);
        s.trip(proto::CAUSE_HOST_REQUEST, true, 100);
        s.ackBroadcast();
        s.onHoverboardStatus(false, 100);
        run(s, 100, 101, false, true, 50);
        CHECK(s.pendingBroadcast() == 1);
        s.ackBroadcast();
        run(s, 101, 150, false, true, 50);
        CHECK(s.pendingBroadcast() == -1);      // rate limited
        s.onHoverboardStatus(true, 150);
        run(s, 150, 400, false, true, 50);
        CHECK(s.pendingBroadcast() == -1);
    }
    {
        std::puts("the first cause is kept when a second one arrives");
        Safety s(CFG);
        s.setEventHandler(quiet);
        s.begin(0, false);
        s.onEstopFrame(true, 10);
        run(s, 10, 100, true, true, 0);
        CHECK(s.cause() == proto::CAUSE_HOST_REQUEST);
        CHECK(s.buttonPressed());
    }
    {
        std::puts("millis() wrap-around");
        Safety s(CFG);
        s.setEventHandler(quiet);
        const uint32_t t0 = 0xFFFFFF00u;
        s.begin(t0, false);
        for (uint32_t i = 0; i < 400; i++) {
            const uint32_t t = t0 + i;
            if (i % 20 == 0) s.onHostCommand(t);
            s.update(t, false);
        }
        CHECK(!s.active());
        CHECK(s.hostArmed());
    }
    {
        std::puts("BOOT_LATCHED: starts latched with the relay open");
        Safety::Config c = CFG;
        c.bootLatched = true;
        Safety s(c);
        s.setEventHandler(quiet);
        s.begin(0, false);
        CHECK(s.active());
        CHECK(!s.relayClosed());
        s.onEstopFrame(false, 50);
        CHECK(s.relayClosed());
    }

    std::printf(failures ? "\n%d check(s) FAILED\n" : "\nall checks passed\n", failures);
    return failures ? 1 : 0;
}
