#include "MCP2515.h"

MCP2515::MCP2515(SPIClass &spi, uint8_t csPin, uint32_t spiHz)
    : spi_(spi), cs_(csPin), settings_(spiHz, MSBFIRST, SPI_MODE0) {}

bool MCP2515::computeTiming(uint32_t crystalHz, uint32_t bitrate, CanTiming &t) {
    // Bit time = tq * TQ, TQ = 2 * (brp + 1) / Fosc  ->  Fosc / (2 * bitrate) = tq * (brp + 1)
    if (bitrate == 0 || crystalHz % (2 * bitrate) != 0) return false;
    const uint32_t ratio = crystalHz / (2 * bitrate);
    // Segments per quanta count. Constraints (datasheet 5.3): PS2 >= 2, prop + PS1 >= PS2, PS2 > SJW.
    // SJW is kept large: the hoverboard board runs from its internal RC oscillator.
    static const uint8_t layouts[][5] = {
        // tq, prop, ps1, ps2, sjw         sample point
        {16, 4, 6, 5, 4},                  // 68.75 %, tolerates ~1.2 % oscillator error
        {10, 2, 4, 3, 2},                  // 70 %
        {8,  1, 3, 3, 2},                  // 62.5 %
    };
    for (const auto &l : layouts) {
        if (ratio % l[0] != 0) continue;
        const uint32_t brp = ratio / l[0] - 1;
        if (brp > 63) continue;
        t.tq = l[0]; t.prop = l[1]; t.ps1 = l[2]; t.ps2 = l[3]; t.sjw = l[4];
        t.brp = (uint8_t)brp;
        t.cnf1 = (uint8_t)(((t.sjw - 1) << 6) | t.brp);
        t.cnf2 = (uint8_t)(0x80 | ((t.ps1 - 1) << 3) | (t.prop - 1));   // BTLMODE = 1: PS2 from CNF3
        t.cnf3 = (uint8_t)(t.ps2 - 1);
        t.samplePermille = (uint16_t)(1000u * (1 + t.prop + t.ps1) / t.tq);
        return true;
    }
    return false;
}

void MCP2515::select() {
    spi_.beginTransaction(settings_);
    digitalWrite(cs_, LOW);
}

void MCP2515::deselect() {
    digitalWrite(cs_, HIGH);
    spi_.endTransaction();
}

void MCP2515::reset() {
    select();
    spi_.transfer(INSTR_RESET);
    deselect();
    delay(5);   // oscillator start-up after reset (128 OSC1 cycles + crystal)
}

uint8_t MCP2515::readReg(uint8_t reg) {
    select();
    spi_.transfer(INSTR_READ);
    spi_.transfer(reg);
    uint8_t v = spi_.transfer(0x00);
    deselect();
    return v;
}

void MCP2515::writeReg(uint8_t reg, uint8_t value) { writeRegs(reg, &value, 1); }

void MCP2515::writeRegs(uint8_t reg, const uint8_t *values, uint8_t n) {
    select();
    spi_.transfer(INSTR_WRITE);
    spi_.transfer(reg);
    for (uint8_t i = 0; i < n; i++) spi_.transfer(values[i]);
    deselect();
}

void MCP2515::modifyReg(uint8_t reg, uint8_t mask, uint8_t value) {
    select();
    spi_.transfer(INSTR_BITMOD);
    spi_.transfer(reg);
    spi_.transfer(mask);
    spi_.transfer(value);
    deselect();
}

uint8_t MCP2515::readStatus() {
    select();
    spi_.transfer(INSTR_READ_STATUS);
    uint8_t v = spi_.transfer(0x00);
    deselect();
    return v;
}

MCP2515::Result MCP2515::begin(uint32_t crystalHz, uint32_t bitrate, Mode mode) {
    ready_ = false;
    rx1First_ = false;
    pinMode(cs_, OUTPUT);
    digitalWrite(cs_, HIGH);
    if (!computeTiming(crystalHz, bitrate, timing_)) return BAD_BITRATE;

    reset();
    if ((readReg(REG_CANSTAT) & 0xE0) != CONFIG) return NO_RESPONSE;   // after reset the chip is in config mode

    const uint8_t cnf[3] = {timing_.cnf3, timing_.cnf2, timing_.cnf1};  // CNF3, CNF2, CNF1 are consecutive
    writeRegs(REG_CNF3, cnf, 3);
    if (readReg(REG_CNF1) != timing_.cnf1 || readReg(REG_CNF2) != timing_.cnf2) return NO_RESPONSE;

    writeReg(REG_RXB0CTRL, 0x64);    // RXM = 11 (receive any frame), BUKT = 1 (roll over into RXB1)
    writeReg(REG_RXB1CTRL, 0x60);    // RXM = 11
    writeReg(REG_BFPCTRL, 0x00);     // RXnBF pins unused
    writeReg(REG_TXRTSCTRL, 0x00);   // TXnRTS pins unused
    writeReg(REG_CANINTE, 0x03);     // INT pin: RX0IF, RX1IF
    writeReg(REG_CANINTF, 0x00);
    writeReg(REG_EFLG, 0x00);

    ready_ = true;
    if (!setMode(mode)) {
        ready_ = false;
        return MODE_TIMEOUT;
    }
    return OK;
}

bool MCP2515::setMode(Mode m) {
    modifyReg(REG_CANCTRL, 0xE0 | 0x08 | 0x04, m);   // REQOP, OSM = 0 (retransmit), CLKEN = 0
    const uint32_t t0 = millis();
    while (millis() - t0 < 10) {
        if ((readReg(REG_CANSTAT) & 0xE0) == m) return true;
    }
    return false;
}

MCP2515::Mode MCP2515::mode() { return (Mode)(readReg(REG_CANSTAT) & 0xE0); }

bool MCP2515::responding(Mode expected) {
    return readReg(REG_CNF1) == timing_.cnf1 && readReg(REG_CNF3) == timing_.cnf3 && mode() == expected;
}

bool MCP2515::send(const CanFrame &f) {
    if (!ready_) return false;
    const uint8_t st = readStatus();
    // READ STATUS bits 2/4/6: TXREQ of TXB0/1/2
    int buf = !(st & 0x04) ? 0 : !(st & 0x10) ? 1 : !(st & 0x40) ? 2 : -1;
    if (buf < 0) return false;

    uint8_t hdr[5];
    const uint8_t len = f.len > 8 ? 8 : f.len;
    if (f.ext) {
        hdr[0] = (uint8_t)(f.id >> 21);
        hdr[1] = (uint8_t)(((f.id >> 13) & 0xE0) | 0x08 | ((f.id >> 16) & 0x03));
        hdr[2] = (uint8_t)(f.id >> 8);
        hdr[3] = (uint8_t)f.id;
    } else {
        hdr[0] = (uint8_t)(f.id >> 3);
        hdr[1] = (uint8_t)((f.id & 0x07) << 5);
        hdr[2] = 0;
        hdr[3] = 0;
    }
    hdr[4] = (uint8_t)(len | (f.rtr ? 0x40 : 0));

    select();
    spi_.transfer((uint8_t)(INSTR_LOAD_TX0 + 2 * buf));   // starts at TXBnSIDH
    for (uint8_t b : hdr) spi_.transfer(b);
    if (!f.rtr) for (uint8_t i = 0; i < len; i++) spi_.transfer(f.data[i]);
    deselect();

    select();
    spi_.transfer((uint8_t)(INSTR_RTS | (1 << buf)));
    deselect();
    return true;
}

void MCP2515::readRxBuffer(uint8_t instr, CanFrame &f) {
    uint8_t h[5];
    select();
    spi_.transfer(instr);   // starts at RXBnSIDH; RXnIF clears when CS rises
    for (uint8_t &b : h) b = spi_.transfer(0x00);
    f.ext = h[1] & 0x08;
    if (f.ext) {
        f.id = ((uint32_t)h[0] << 21) | ((uint32_t)(h[1] & 0xE0) << 13) | ((uint32_t)(h[1] & 0x03) << 16) |
               ((uint32_t)h[2] << 8) | h[3];
        f.rtr = h[4] & 0x40;
    } else {
        f.id = ((uint32_t)h[0] << 3) | (h[1] >> 5);
        f.rtr = h[1] & 0x10;    // SRR
    }
    f.len = h[4] & 0x0F;
    if (f.len > 8) f.len = 8;
    for (uint8_t i = 0; i < 8; i++) f.data[i] = i < f.len ? spi_.transfer(0x00) : 0;
    deselect();
}

bool MCP2515::receive(CanFrame &f) {
    if (!ready_) return false;
    const uint8_t st = readStatus();
    // RXB1 only fills while RXB0 is full, so with both full RXB0 is older; after reading it, RXB1
    // must go next even if a newer frame has meanwhile landed in RXB0 (keeps e-stop/release order).
    if ((st & 0x02) && (rx1First_ || !(st & 0x01))) {
        readRxBuffer(INSTR_READ_RX1, f);
        rx1First_ = false;
        return true;
    }
    if (st & 0x01) {
        readRxBuffer(INSTR_READ_RX0, f);
        rx1First_ = st & 0x02;
        return true;
    }
    return false;
}

void MCP2515::clearOverflow() { modifyReg(REG_EFLG, EFLG_RX0OVR | EFLG_RX1OVR, 0); }
