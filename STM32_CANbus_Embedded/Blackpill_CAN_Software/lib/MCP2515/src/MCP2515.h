/*
 * Minimal MCP2515 stand-alone CAN controller driver (SPI, standard and extended frames).
 *
 * - Bit timing computed from the crystal and bitrate (8, 10 or 16 time quanta per bit).
 * - Both receive buffers accept every frame (rollover from RXB0 into RXB1); filter in software.
 * - Three transmit buffers, automatic retransmission, automatic bus-off recovery (MCP2515 hardware).
 */
#pragma once

#include <Arduino.h>
#include <SPI.h>

struct CanFrame {
    uint32_t id = 0;
    uint8_t len = 0;
    bool ext = false;
    bool rtr = false;
    uint8_t data[8] = {0};
};

struct CanTiming {
    uint8_t cnf1, cnf2, cnf3;
    uint8_t brp;            // baud rate prescaler register value (TQ = 2 * (brp + 1) / Fosc)
    uint8_t tq;             // time quanta per bit
    uint8_t prop, ps1, ps2, sjw;
    uint16_t samplePermille;
};

class MCP2515 {
public:
    enum Mode : uint8_t { NORMAL = 0x00, SLEEP = 0x20, LOOPBACK = 0x40, LISTEN_ONLY = 0x60, CONFIG = 0x80 };
    enum Result : uint8_t { OK = 0, NO_RESPONSE, BAD_BITRATE, MODE_TIMEOUT };

    // EFLG bits
    static constexpr uint8_t EFLG_RX1OVR = 0x80, EFLG_RX0OVR = 0x40, EFLG_TXBO = 0x20, EFLG_TXEP = 0x10,
                             EFLG_RXEP = 0x08, EFLG_TXWAR = 0x04, EFLG_RXWAR = 0x02, EFLG_EWARN = 0x01;

    MCP2515(SPIClass &spi, uint8_t csPin, uint32_t spiHz);

    // Resets the chip, sets the bit timing and enters `mode`. Can be called again to re-initialise.
    Result begin(uint32_t crystalHz, uint32_t bitrate, Mode mode = NORMAL);
    bool setMode(Mode mode);
    Mode mode();
    // True if the chip answers with the configured bit timing and is in `expected` mode
    // (catches an unplugged module even when MISO floats low).
    bool responding(Mode expected);

    bool send(const CanFrame &f);        // false: all three transmit buffers busy (or not initialised)
    bool receive(CanFrame &f);           // false: nothing received

    uint8_t tec() { return readReg(REG_TEC); }
    uint8_t rec() { return readReg(REG_REC); }
    uint8_t eflg() { return readReg(REG_EFLG); }
    void clearOverflow();                // clears RX0OVR/RX1OVR (the frames are lost)

    // Pure function, also used for the start-up report. False if the bitrate is not reachable.
    static bool computeTiming(uint32_t crystalHz, uint32_t bitrate, CanTiming &t);
    const CanTiming &timing() const { return timing_; }

private:
    static constexpr uint8_t INSTR_RESET = 0xC0, INSTR_READ = 0x03, INSTR_WRITE = 0x02, INSTR_BITMOD = 0x05,
                             INSTR_READ_STATUS = 0xA0, INSTR_READ_RX0 = 0x90, INSTR_READ_RX1 = 0x94,
                             INSTR_LOAD_TX0 = 0x40, INSTR_RTS = 0x80;
    static constexpr uint8_t REG_CANSTAT = 0x0E, REG_CANCTRL = 0x0F, REG_TEC = 0x1C, REG_REC = 0x1D,
                             REG_CNF3 = 0x28, REG_CNF2 = 0x29, REG_CNF1 = 0x2A, REG_CANINTE = 0x2B,
                             REG_CANINTF = 0x2C, REG_EFLG = 0x2D, REG_TXB0CTRL = 0x30, REG_RXB0CTRL = 0x60,
                             REG_RXB1CTRL = 0x70, REG_BFPCTRL = 0x0C, REG_TXRTSCTRL = 0x0D;

    void select();
    void deselect();
    void reset();
    uint8_t readReg(uint8_t reg);
    void writeReg(uint8_t reg, uint8_t value);
    void writeRegs(uint8_t reg, const uint8_t *values, uint8_t n);
    void modifyReg(uint8_t reg, uint8_t mask, uint8_t value);
    uint8_t readStatus();
    void readRxBuffer(uint8_t instr, CanFrame &f);

    SPIClass &spi_;
    uint8_t cs_;
    SPISettings settings_;
    CanTiming timing_{};
    bool ready_ = false;
    bool rx1First_ = false;
};
