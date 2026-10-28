/**
  * This file is part of the hoverboard-firmware-hack project.
  *
  * This program is free software: you can redistribute it and/or modify
  * it under the terms of the GNU General Public License as published by
  * the Free Software Foundation, either version 3 of the License, or
  * (at your option) any later version.
  *
  * This program is distributed in the hope that it will be useful,
  * but WITHOUT ANY WARRANTY; without even the implied warranty of
  * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  * GNU General Public License for more details.
  *
  * You should have received a copy of the GNU General Public License
  * along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

/*
 * bxCAN driver (register level, CMSIS only) and the AGV CAN protocol (see CAN SETTINGS in config.h).
 * Implemented without the HAL CAN module on purpose: the bundled Drivers/ folder ships the legacy HAL CAN API,
 * while PlatformIO's framework-stm32cubef1 ships the new one. Register access works with both.
 */

#include "stm32f1xx_hal.h"
#include "defines.h"
#include "config.h"
#include "util.h"
#include "BLDC_controller.h"
#include "rtwtypes.h"
#include "can.h"

#if defined(CONTROL_CAN) || defined(FEEDBACK_CAN)

// Bit timing: 1 (sync) + BS1 + BS2 = 16 time quanta, sample point 75 %, SJW 4.
// The board runs from the internal HSI RC oscillator (no crystal). Against crystal-clocked nodes (Pi adapter, F411)
// this allows ~2 % clock deviation (CAN tolerance rules: 2*df*(13*16 - 4) <= 4 and 2*df*10*16 <= 4), while BS1 leaves
// ~400 ns at 1 Mbit/s for the transceiver loop delay. HSI spec: +-1 % at 25 degC, ~+-2 % over temperature.
#define CAN_TQ_PER_BIT    16
#define CAN_BS1_TQ        11
#define CAN_BS2_TQ        4
#define CAN_SJW_TQ        4
#define CAN_INIT_TIMEOUT  10      // [ms]

// PCLK1 = 64 MHz / 2 = 32 MHz (see SystemClock_Config in main.c)
#if (32000000UL % (CAN_BITRATE * CAN_TQ_PER_BIT)) != 0
  #error CAN_BITRATE not reachable with PCLK1 = 32 MHz. Use 125000, 250000, 500000 or 1000000.
#endif

// Wheel direction: feedback uses the same "+ = robot forward" convention as the commands (see main.c output stage)
#ifdef INVERT_L_DIRECTION
  #define CAN_DIR_L       (-1)
#else
  #define CAN_DIR_L       (1)
#endif
#ifdef INVERT_R_DIRECTION
  #define CAN_DIR_R       (1)
#else
  #define CAN_DIR_R       (-1)
#endif

//------------------------------------------------------------------------
// Global variables set externally
//------------------------------------------------------------------------
extern P    rtP_Left;
extern ExtY rtY_Left;
extern ExtY rtY_Right;

extern uint8_t  enable;
extern uint8_t  inIdx;
extern int16_t  batVoltage;
extern int16_t  batVoltageCalib;
extern int16_t  board_temp_deg_c;
extern uint8_t  timeoutFlgADC;
extern uint8_t  timeoutFlgSerial;
extern uint8_t  timeoutFlgCAN;
extern volatile uint8_t  timeoutFlgGen;
extern volatile uint32_t main_loop_counter;

extern volatile int32_t  odomTicksL;           // hall step counters, updated in the 16 kHz motor ISR (bldc.c)
extern volatile int32_t  odomTicksR;
extern volatile uint32_t curL_DC_sum;          // DC current accumulators, updated in the 16 kHz motor ISR (bldc.c)
extern volatile uint32_t curR_DC_sum;
extern volatile uint32_t curDC_cnt;

//------------------------------------------------------------------------
// Local variables
//------------------------------------------------------------------------
#ifdef CONTROL_CAN
static volatile uint32_t canCmdData = 0;      // last valid command payload (bytes 0..3), written atomically by the ISR
static volatile uint32_t canCmdCnt  = 0;      // number of valid command frames received
static volatile uint8_t  canEstop   = 0;      // e-stop latch
#endif

// Li-ion cell open-circuit voltage [mV] at 100, 95, ..., 5, 0 %
static const uint16_t batCellCurve[21] = {
  4200, 4150, 4110, 4080, 4020, 3980, 3950, 3910, 3870, 3850, 3840,
  3820, 3800, 3790, 3770, 3750, 3730, 3710, 3690, 3610, 3270
};


static void CAN_GPIO_Init(void) {
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_AFIO_CLK_ENABLE();

#if CAN_PINS == 1
  __HAL_RCC_GPIOB_CLK_ENABLE();
  // CAN_REMAP = 10: CAN_RX -> PB8, CAN_TX -> PB9. SWJ_CFG written as 000 (reset value, full SWD/JTAG kept)
  AFIO->MAPR = (AFIO->MAPR & ~(AFIO_MAPR_CAN_REMAP | AFIO_MAPR_SWJ_CFG)) | AFIO_MAPR_CAN_REMAP_REMAP2;

  GPIO_InitStruct.Pin   = GPIO_PIN_8;
  GPIO_InitStruct.Mode  = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull  = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin   = GPIO_PIN_9;
  GPIO_InitStruct.Mode  = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull  = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);
#else
  __HAL_RCC_GPIOA_CLK_ENABLE();
  // CAN_REMAP = 00: CAN_RX -> PA11, CAN_TX -> PA12
  AFIO->MAPR = (AFIO->MAPR & ~(AFIO_MAPR_CAN_REMAP | AFIO_MAPR_SWJ_CFG));

  GPIO_InitStruct.Pin   = GPIO_PIN_11;
  GPIO_InitStruct.Mode  = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull  = GPIO_PULLUP;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  GPIO_InitStruct.Pin   = GPIO_PIN_12;
  GPIO_InitStruct.Mode  = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull  = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
#endif
}

void CAN_Init(void) {
  uint32_t tickstart;
  uint32_t prescaler;

  CAN_GPIO_Init();

  RCC->APB1ENR |= RCC_APB1ENR_CAN1EN;
  (void)RCC->APB1ENR;                                     // delay after RCC peripheral clock enabling
  RCC->APB1RSTR |=  RCC_APB1RSTR_CAN1RST;
  RCC->APB1RSTR &= ~RCC_APB1RSTR_CAN1RST;

  // Exit sleep mode and request initialization mode
  CAN1->MCR &= ~CAN_MCR_SLEEP;
  CAN1->MCR |=  CAN_MCR_INRQ;
  tickstart = HAL_GetTick();
  while (!(CAN1->MSR & CAN_MSR_INAK) && (HAL_GetTick() - tickstart) < CAN_INIT_TIMEOUT) { }

  // Automatic bus-off recovery, automatic retransmission, transmit in chronological order
  CAN1->MCR = CAN_MCR_INRQ | CAN_MCR_ABOM | CAN_MCR_TXFP;

  prescaler = HAL_RCC_GetPCLK1Freq() / ((uint32_t)CAN_BITRATE * CAN_TQ_PER_BIT);
  CAN1->BTR = ((uint32_t)(CAN_SJW_TQ - 1) << 24) |
              ((uint32_t)(CAN_BS2_TQ - 1) << 20) |
              ((uint32_t)(CAN_BS1_TQ - 1) << 16) |
              (prescaler - 1);

  // Acceptance filter: bank 0, 32-bit identifier list {CAN_ID_CMD, CAN_ID_ESTOP} -> FIFO 0
  CAN1->FMR  |=  CAN_FMR_FINIT;
  CAN1->FA1R &= ~1UL;                                     // deactivate filter bank 0
#ifdef CONTROL_CAN
  CAN1->FS1R  |=  1UL;                                    // 32-bit scale
  CAN1->FM1R  |=  1UL;                                    // identifier list mode
  CAN1->FFA1R &= ~1UL;                                    // assign to FIFO 0
  CAN1->sFilterRegister[0].FR1 = (uint32_t)CAN_ID_CMD   << 21;   // standard ID, data frame
  CAN1->sFilterRegister[0].FR2 = (uint32_t)CAN_ID_ESTOP << 21;
  CAN1->FA1R  |=  1UL;                                    // activate filter bank 0
#endif
  CAN1->FMR  &= ~CAN_FMR_FINIT;

#ifdef CONTROL_CAN
  CAN1->IER |= CAN_IER_FMPIE0;                            // interrupt on FIFO 0 message pending
  HAL_NVIC_SetPriority(USB_LP_CAN1_RX0_IRQn, 1, 0);       // lower priority than the motor control ISR
  HAL_NVIC_EnableIRQ(USB_LP_CAN1_RX0_IRQn);
#endif

  // Leave initialization mode. Synchronization needs 11 recessive bits on the bus (RX pulled up if no transceiver is present)
  CAN1->MCR &= ~CAN_MCR_INRQ;
  tickstart = HAL_GetTick();
  while ((CAN1->MSR & CAN_MSR_INAK) && (HAL_GetTick() - tickstart) < CAN_INIT_TIMEOUT) { }
}

/*
 * Send a standard (11-bit) data frame. Non-blocking.
 * Returns 1 if the frame was queued, 0 otherwise.
 */
uint8_t CAN_Send(uint16_t stdId, const uint8_t *data, uint8_t len) {
  uint32_t tsr = CAN1->TSR;
  uint32_t lo  = 0;
  uint32_t hi  = 0;
  uint8_t  mb;

  if (tsr & CAN_TSR_TME0) {
    mb = 0;
  } else if (tsr & CAN_TSR_TME1) {
    mb = 1;
  } else if (tsr & CAN_TSR_TME2) {
    mb = 2;
  } else {
    // All mailboxes pending (e.g. no other node ACKs the frames): drop the stale frames so fresh data goes out next time
    CAN1->TSR = CAN_TSR_ABRQ0 | CAN_TSR_ABRQ1 | CAN_TSR_ABRQ2;
    return 0;
  }

  if (len > 8) {
    len = 8;
  }
  for (uint8_t i = 0; i < len; i++) {
    if (i < 4) {
      lo |= (uint32_t)data[i] << (8 * i);
    } else {
      hi |= (uint32_t)data[i] << (8 * (i - 4));
    }
  }

  CAN1->sTxMailBox[mb].TDTR = len;
  CAN1->sTxMailBox[mb].TDLR = lo;
  CAN1->sTxMailBox[mb].TDHR = hi;
  CAN1->sTxMailBox[mb].TIR  = ((uint32_t)(stdId & 0x7FF) << 21) | CAN_TI0R_TXRQ;
  return 1;
}


/* =========================== Feedback =========================== */

static void putU16(uint8_t *buf, uint16_t val) {
  buf[0] = (uint8_t)(val & 0xFF);
  buf[1] = (uint8_t)(val >> 8);
}

static void putU32(uint8_t *buf, uint32_t val) {
  putU16(buf,     (uint16_t)(val & 0xFFFF));
  putU16(buf + 2, (uint16_t)(val >> 16));
}

// Apply the wheel direction to a wrapping 32-bit counter (unsigned arithmetic: no overflow on negation)
static uint32_t dirU32(uint32_t val, int8_t dir) {
  return (dir < 0) ? (0U - val) : val;
}

static uint8_t batteryPercent(int16_t voltage_x100) {
  int32_t cell_mV = ((int32_t)voltage_x100 * 10) / BAT_CELLS;

  if (cell_mV >= batCellCurve[0]) {
    return 100;
  }
  for (uint8_t i = 1; i < 21; i++) {
    if (cell_mV >= batCellCurve[i]) {
      int32_t hiV = batCellCurve[i - 1], loV = batCellCurve[i];
      int32_t hiP = 100 - 5 * (i - 1),   loP = 100 - 5 * i;
      return (uint8_t)(loP + ((cell_mV - loV) * (hiP - loP)) / (hiV - loV));
    }
  }
  return 0;
}

/*
 * Called once per main loop. Sends odometry + motion frames every CAN_FEEDBACK_PERIOD loops and
 * the status frame every CAN_STATUS_PERIOD loops.
 */
void CAN_FeedbackTask(void) {
#ifdef FEEDBACK_CAN
  static uint32_t sumLPrev = 0, sumRPrev = 0, cntPrev = 0;
  static uint8_t  heartbeat = 0;
  uint8_t data[8];

  if (main_loop_counter % CAN_FEEDBACK_PERIOD == 0) {
    // Odometry: hall steps since power-on, signed, wrapping
    putU32(&data[0], dirU32((uint32_t)odomTicksL, CAN_DIR_L));
    putU32(&data[4], dirU32((uint32_t)odomTicksR, CAN_DIR_R));
    CAN_Send(CAN_ID_ODOM, data, 8);

    // DC currents averaged over all motor ISR samples since the previous frame
    uint32_t sumL = curL_DC_sum, sumR = curR_DC_sum, cnt = curDC_cnt;
    int32_t  n    = (int32_t)(cnt - cntPrev);
    int32_t  avgL = 0, avgR = 0;
    if (n > 0) {
      avgL = (int32_t)(sumL - sumLPrev) / n;
      avgR = (int32_t)(sumR - sumRPrev) / n;
    }
    sumLPrev = sumL; sumRPrev = sumR; cntPrev = cnt;

    putU16(&data[0], (uint16_t)(int16_t)(CAN_DIR_L * rtY_Left.n_mot));
    putU16(&data[2], (uint16_t)(int16_t)(CAN_DIR_R * rtY_Right.n_mot));
    putU16(&data[4], (uint16_t)(int16_t)(-(avgL * 100) / A2BIT_CONV));    // same scaling as left_dc_curr in main.c
    putU16(&data[6], (uint16_t)(int16_t)(-(avgR * 100) / A2BIT_CONV));
    CAN_Send(CAN_ID_MOTION, data, 8);
  }

  if (main_loop_counter % CAN_STATUS_PERIOD == 0) {
    uint8_t  status = 0;
    uint32_t esr    = CAN1->ESR;
    uint8_t  tec    = (uint8_t)((esr & CAN_ESR_TEC) >> 16);
    uint8_t  rec    = (uint8_t)((esr & CAN_ESR_REC) >> 24);
    int16_t  temp   = CLAMP(board_temp_deg_c / 10, -128, 127);

    if (enable && !rtY_Left.z_errCode && !rtY_Right.z_errCode) { status |= CAN_STATUS_ENABLED; }
    if (timeoutFlgCAN)                                         { status |= CAN_STATUS_CMD_TIMEOUT; }
    if (timeoutFlgADC || timeoutFlgSerial || timeoutFlgGen)    { status |= CAN_STATUS_SAFE_STATE; }
    #ifdef CONTROL_CAN
    if (canEstop)                                              { status |= CAN_STATUS_ESTOP; }
    #endif
    if (inIdx)                                                 { status |= CAN_STATUS_AUX_INPUT; }
    if (batVoltage < BAT_LVL1)                                 { status |= CAN_STATUS_BAT_LOW; }
    if (board_temp_deg_c >= TEMP_WARNING)                      { status |= CAN_STATUS_TEMP_WARN; }

    putU16(&data[0], (uint16_t)batVoltageCalib);
    data[2] = batteryPercent(batVoltageCalib);
    data[3] = (uint8_t)(int8_t)temp;
    data[4] = (uint8_t)((rtY_Left.z_errCode & 0x0F) | ((rtY_Right.z_errCode & 0x0F) << 4));
    data[5] = status;
    data[6] = (tec > rec) ? tec : rec;
    data[7] = heartbeat++;
    CAN_Send(CAN_ID_STATUS, data, 8);
  }
#endif
}


/* =========================== Commands =========================== */

#ifdef CONTROL_CAN
/*
 * Latest command values (little-endian int16 in bytes 0-1 and 2-3 of the CAN_ID_CMD frame),
 * converted to the normalized input range [-1000, 1000]. In SPD_MODE the values are wheel speeds in rpm.
 */
void CAN_GetCommand(int16_t *in1, int16_t *in2) {
  uint32_t data = canCmdData;
  int32_t  cmd1 = (int16_t)(data & 0xFFFF);
  int32_t  cmd2 = (int16_t)(data >> 16);

  #if CTRL_MOD_REQ == SPD_MODE
    int32_t nMax = rtP_Left.n_max >> 4;                   // [rpm], speed target = input * n_max / 1000 in the FOC controller
    if (nMax < 1) {
      nMax = 1;
    }
    cmd1 = (cmd1 * 1000) / nMax;
    cmd2 = (cmd2 * 1000) / nMax;
  #endif

  *in1 = (int16_t)CLAMP(cmd1, -1000, 1000);
  *in2 = (int16_t)CLAMP(cmd2, -1000, 1000);
}

/*
 * Returns 1 if at least one new valid command frame arrived since the previous call.
 */
uint8_t CAN_CommandReceived(void) {
  static uint32_t cntPrev = 0;
  uint32_t cnt = canCmdCnt;
  uint8_t  ret = (cnt != cntPrev);
  cntPrev = cnt;
  return ret;
}

uint8_t CAN_EstopActive(void) {
  return canEstop;
}

/*
 * Called from USB_LP_CAN1_RX0_IRQHandler
 */
void CAN_RX0_IRQ(void) {
  while (CAN1->RF0R & CAN_RF0R_FMP0) {
    uint32_t rir  = CAN1->sFIFOMailBox[0].RIR;
    uint32_t dlc  = CAN1->sFIFOMailBox[0].RDTR & CAN_RDT0R_DLC;
    uint32_t data = CAN1->sFIFOMailBox[0].RDLR;
    uint32_t id   = rir >> 21;
    CAN1->RF0R = CAN_RF0R_RFOM0;                          // release FIFO 0 output mailbox

    if (rir & (CAN_RI0R_IDE | CAN_RI0R_RTR)) {
      continue;
    }
    if (id == CAN_ID_CMD && dlc >= 4) {
      canCmdData = data;
      canCmdCnt++;
    } else if (id == CAN_ID_ESTOP && dlc >= 1) {
      canEstop = (uint8_t)(data & 0x01);
    }
  }
}
#endif

#endif
