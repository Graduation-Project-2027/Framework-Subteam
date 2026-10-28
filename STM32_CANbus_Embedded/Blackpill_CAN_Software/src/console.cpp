#include "console.h"

#include <Arduino.h>
#include <IWatchdog.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "app.h"
#include "bootloader.h"
#include "config.h"
#include "protocol.h"

namespace console {
namespace {

enum class Trace : uint8_t { OFF, RX, ALL };

char line_[96];
uint8_t lineLen_ = 0;
Trace trace_ = Trace::OFF;
bool stream_ = false;
uint32_t lastStream_ = 0;
bool stalled_ = false;

constexpr int TX_QUEUE = 127;   // USB CDC transmit queue of the core (2 packets - 1)

// Writes in chunks as the USB queue drains, for at most waitMs. Gives up (and stays quiet until the
// queue is empty again) when the host does not read, so the safety loop is never held up.
void out(const char *s, size_t n, uint32_t waitMs) {
    if (stalled_) {
        if (Serial.availableForWrite() < TX_QUEUE) { app::stats.consoleDrops++; return; }
        stalled_ = false;
    }
    const uint32_t t0 = millis();
    while (n > 0) {
        int room = Serial.availableForWrite();
        if (room > 0) {
            size_t k = (size_t)room < n ? (size_t)room : n;
            size_t w = Serial.write(reinterpret_cast<const uint8_t *>(s), k);
            if (w == 0) return;   // not connected
            s += w;
            n -= w;
            continue;
        }
        if (millis() - t0 >= waitMs) {
            stalled_ = true;
            app::stats.consoleDrops++;
            return;
        }
        IWatchdog.reload();
    }
}

void vout(uint32_t waitMs, const char *fmt, va_list ap) {
    char buf[200];
    int n = vsnprintf(buf, sizeof buf, fmt, ap);
    if (n < 0) return;
    out(buf, (size_t)n < sizeof buf ? (size_t)n : sizeof buf - 1, waitMs);
}

// Command replies wait longer than unsolicited output.
void reply(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void reply(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vout(60, fmt, ap);
    va_end(ap);
}

void stamp(char *buf, size_t n) {
    uint32_t ms = millis();
    snprintf(buf, n, "[%6lu.%03lu] ", (unsigned long)(ms / 1000), (unsigned long)(ms % 1000));
}

const char *onOff(bool v) { return v ? "on" : "off"; }

void printStatus(uint32_t now) {
    auto &s = app::safety;
    if (s.active())
        reply("E-stop     : ACTIVE, cause %s\r\n", proto::causeName(s.cause()));
    else
        reply("E-stop     : released (last cause: %s)\r\n", proto::causeName(s.cause()));
    reply("Relay      : %s\r\n", s.relayClosed() ? "closed, hoverboard powered" : "OPEN, hoverboard unpowered");
    reply("Button     : %s\r\n", s.buttonPressed() ? "PRESSED" : "released");
    uint32_t age = s.hostAge(now);
    if (age == UINT32_MAX)
        reply("Host       : no HB_CMD seen yet, watchdog %s\r\n", s.hostArmed() ? "armed" : "not armed");
    else
        reply("Host       : last HB_CMD %lu ms ago, watchdog %s\r\n", (unsigned long)age,
              s.hostArmed() ? "armed" : "not armed");
    if (s.motionFresh(now))
        reply("Hoverboard : wheels %d / %d rpm", s.rpmLeft(), s.rpmRight());
    else
        reply("Hoverboard : no motion frames");
    if (s.hbStatusFresh(now))
        reply(", reports e-stop %s\r\n", s.hbEstop() ? "latched" : "not latched");
    else
        reply(", no status frames\r\n");
    if (app::canReady())
        reply("CAN        : ok, %lu kbit/s, TEC %u, REC %u\r\n", (unsigned long)(CAN_BITRATE / 1000),
              app::can.tec(), app::can.rec());
    else
        reply("CAN        : MCP2515 NOT READY (retrying every %u ms)\r\n", CAN_RETRY_MS);
    reply("Uptime     : %lu s\r\n", (unsigned long)(now / 1000));
}

void printCan() {
    const CanTiming &t = app::can.timing();
    reply("Bitrate    : %lu bit/s, MCP2515 crystal %lu MHz\r\n", (unsigned long)CAN_BITRATE,
          (unsigned long)(MCP2515_CRYSTAL_HZ / 1000000));
    reply("Timing     : %u TQ (BRP %u, prop %u, PS1 %u, PS2 %u, SJW %u), sample point %u.%u %%\r\n", t.tq, t.brp,
          t.prop, t.ps1, t.ps2, t.sjw, t.samplePermille / 10, t.samplePermille % 10);
    reply("Registers  : CNF1 0x%02X, CNF2 0x%02X, CNF3 0x%02X\r\n", t.cnf1, t.cnf2, t.cnf3);
    if (!app::canReady()) {
        reply("State      : MCP2515 NOT READY\r\n");
        return;
    }
    uint8_t e = app::can.eflg();
    reply("State      : mode 0x%02X, TEC %u, REC %u, EFLG 0x%02X%s%s%s%s\r\n", app::can.mode(), app::can.tec(),
          app::can.rec(), e, (e & MCP2515::EFLG_TXBO) ? " bus-off" : "", (e & MCP2515::EFLG_TXEP) ? " tx-passive" : "",
          (e & MCP2515::EFLG_RXEP) ? " rx-passive" : "", (e & MCP2515::EFLG_EWARN) ? " warning" : "");
    reply("Node       : BODY, hoverboard node %u (HB_CMD 0x%03X, motion 0x%03X, status 0x%03X)\r\n",
          HOVERBOARD_NODE_ID, proto::ID_HB_CMD_BASE + HOVERBOARD_NODE_ID, proto::ID_HB_MOTION_BASE + HOVERBOARD_NODE_ID,
          proto::ID_HB_STATUS_BASE + HOVERBOARD_NODE_ID);
}

void printStats() {
    const Stats &s = app::stats;
    reply("rx %lu, tx %lu, tx failed %lu, rx overflow %lu, bus-off %lu, CAN init failures %lu, console drops %lu\r\n",
          (unsigned long)s.rx, (unsigned long)s.tx, (unsigned long)s.txFail, (unsigned long)s.rxOverflow,
          (unsigned long)s.busOff, (unsigned long)s.canInitFail, (unsigned long)s.consoleDrops);
}

void printHelp() {
    reply("Commands:\r\n");
    reply("  status              e-stop, relay, button, host link, hoverboard, CAN\r\n");
    reply("  can                 bitrate, bit timing, controller state\r\n");
    reply("  stats               frame and error counters\r\n");
    reply("  estop               latch the e-stop (broadcasts ESTOP=1)\r\n");
    reply("  release             release the e-stop (broadcasts ESTOP=0; refused while the button is pressed)\r\n");
    reply("  log off|rx|all      trace received (rx) or all CAN frames\r\n");
    reply("  send ID [B0..B7]    send a standard frame, hex: send 010 01\r\n");
    reply("  selftest            MCP2515 loopback test (bus traffic paused for a few ms)\r\n");
    reply("  stream on|off       machine-readable @S status lines (used by the Blackpill Tool)\r\n");
    reply("  version             firmware and build information\r\n");
    reply("  dfu                 reboot into the USB DFU bootloader\r\n");
    reply("  reboot              restart the controller\r\n");
}

void printVersion() {
    reply("%s %s, built %s %s, CAN %lu bit/s, MCP2515 %lu MHz\r\n", FW_NAME, FW_VERSION, __DATE__, __TIME__,
          (unsigned long)CAN_BITRATE, (unsigned long)(MCP2515_CRYSTAL_HZ / 1000000));
}

bool parseHex(const char *s, uint32_t &v) {
    if (!s || !*s) return false;
    char *end;
    v = strtoul(s, &end, 16);
    return *end == 0;
}

void cmdSend(char **argv, int argc) {
    uint32_t id;
    if (argc < 2 || !parseHex(argv[1], id) || id > 0x7FF || argc > 10) {
        reply("usage: send ID [B0..B7]   (hex, standard ID up to 7FF)\r\n");
        return;
    }
    CanFrame f;
    f.id = id;
    f.len = (uint8_t)(argc - 2);
    for (int i = 0; i < f.len; i++) {
        uint32_t b;
        if (!parseHex(argv[2 + i], b) || b > 0xFF) {
            reply("bad byte '%s'\r\n", argv[2 + i]);
            return;
        }
        f.data[i] = (uint8_t)b;
    }
    reply(app::sendFrame(f) ? "sent\r\n" : "not sent (CAN not ready or transmit buffers full)\r\n");
}

void run(char *cmdline, uint32_t now) {
    char *argv[12];
    int argc = 0;
    for (char *tok = strtok(cmdline, " \t"); tok && argc < 12; tok = strtok(nullptr, " \t")) argv[argc++] = tok;
    if (argc == 0) return;
    for (char *p = argv[0]; *p; p++) *p = (char)tolower(*p);
    const char *c = argv[0];
    const char *arg = argc > 1 ? argv[1] : "";

    if (!strcmp(c, "help") || !strcmp(c, "?")) printHelp();
    else if (!strcmp(c, "status")) printStatus(now);
    else if (!strcmp(c, "can")) printCan();
    else if (!strcmp(c, "stats")) printStats();
    else if (!strcmp(c, "version")) printVersion();
    else if (!strcmp(c, "estop")) {
        app::safety.trip(proto::CAUSE_HOST_REQUEST, true, now);
        reply("e-stop latched\r\n");
    } else if (!strcmp(c, "release")) {
        if (app::safety.release(now)) reply(app::sendEstop(false) ? "released, ESTOP=0 sent\r\n" : "released, ESTOP=0 NOT sent\r\n");
        else reply("refused: e-stop button is pressed\r\n");
    } else if (!strcmp(c, "log")) {
        if (!strcmp(arg, "off")) trace_ = Trace::OFF;
        else if (!strcmp(arg, "all")) trace_ = Trace::ALL;
        else if (!strcmp(arg, "rx") || !strcmp(arg, "on")) trace_ = Trace::RX;
        else trace_ = trace_ == Trace::OFF ? Trace::RX : Trace::OFF;
        reply("CAN trace %s\r\n", trace_ == Trace::OFF ? "off" : trace_ == Trace::RX ? "rx" : "rx+tx");
    } else if (!strcmp(c, "stream")) {
        stream_ = !strcmp(arg, "on") || (strcmp(arg, "off") && !stream_);
        reply("stream %s\r\n", onOff(stream_));
    } else if (!strcmp(c, "send")) cmdSend(argv, argc);
    else if (!strcmp(c, "selftest")) {
        char msg[96];
        bool ok = app::canSelfTest(msg, sizeof msg);
        reply("selftest %s: %s\r\n", ok ? "PASSED" : "FAILED", msg);
    } else if (!strcmp(c, "dfu")) {
        reply("Rebooting into the DFU bootloader...\r\n");
        rebootToDfu();
    } else if (!strcmp(c, "reboot")) {
        reply("Rebooting...\r\n");
        delay(20);
        NVIC_SystemReset();
    } else reply("unknown command '%s', type help\r\n", c);
}

void streamLine(uint32_t now) {
    auto &s = app::safety;
    uint32_t age = s.hostAge(now);
    char buf[220];
    int n = snprintf(buf, sizeof buf,
                     "@S up=%lu active=%u power=%u button=%u cause=%u armed=%u host_ms=%ld motion=%u rpm_l=%d "
                     "rpm_r=%d hb=%u hb_estop=%u can=%u tec=%u rec=%u rx=%lu tx=%lu\r\n",
                     (unsigned long)now, s.active(), s.relayClosed(), s.buttonPressed(), s.cause(), s.hostArmed(),
                     age == UINT32_MAX ? -1L : (long)age, s.motionFresh(now), s.rpmLeft(), s.rpmRight(),
                     s.hbStatusFresh(now), s.hbEstop(), app::canReady(), app::canReady() ? app::can.tec() : 0,
                     app::canReady() ? app::can.rec() : 0, (unsigned long)app::stats.rx, (unsigned long)app::stats.tx);
    if (n > 0) out(buf, (size_t)n < sizeof buf ? (size_t)n : sizeof buf - 1, 10);
}

}  // namespace

void begin() {
    Serial.begin(115200);
}

void printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vout(10, fmt, ap);
    va_end(ap);
}

void event(const char *msg) {
    char buf[200];
    stamp(buf, sizeof buf);
    size_t n = strlen(buf);
    snprintf(buf + n, sizeof buf - n, "%s\r\n", msg);
    out(buf, strlen(buf), 10);
}

void onFrame(const CanFrame &f, bool tx) {
    if (trace_ == Trace::OFF || (tx && trace_ != Trace::ALL)) return;
    char buf[80];
    stamp(buf, sizeof buf);
    size_t n = strlen(buf);
    n += snprintf(buf + n, sizeof buf - n, "%s %03lX [%u]", tx ? "TX" : "RX", (unsigned long)f.id, f.len);
    for (uint8_t i = 0; i < f.len && n < sizeof buf - 4; i++) n += snprintf(buf + n, sizeof buf - n, " %02X", f.data[i]);
    snprintf(buf + n, sizeof buf - n, "\r\n");
    out(buf, strlen(buf), 5);
}

void poll(uint32_t now) {
    while (Serial.available() > 0) {
        int ch = Serial.read();
        if (ch == '\r' || ch == '\n') {
            if (lineLen_ > 0) {
                line_[lineLen_] = 0;
                lineLen_ = 0;
                run(line_, now);
            }
        } else if (ch == 0x08 || ch == 0x7F) {
            if (lineLen_ > 0) lineLen_--;
        } else if (ch >= 0x20 && lineLen_ < sizeof line_ - 1) {
            line_[lineLen_++] = (char)ch;
        }
    }
    if (stream_ && now - lastStream_ >= STREAM_PERIOD_MS) {
        lastStream_ = now;
        streamLine(now);
    }
}

}  // namespace console
