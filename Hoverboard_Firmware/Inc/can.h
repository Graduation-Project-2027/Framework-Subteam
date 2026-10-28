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

// Define to prevent recursive inclusion
#ifndef CAN_H
#define CAN_H

#include <stdint.h>
#include "config.h"

#if defined(CONTROL_CAN) || defined(FEEDBACK_CAN)

// Status bits of the CAN_ID_STATUS frame (byte 5)
#define CAN_STATUS_ENABLED        0x01    // motors enabled
#define CAN_STATUS_CMD_TIMEOUT    0x02    // no valid CAN command received within CAN_TIMEOUT
#define CAN_STATUS_SAFE_STATE     0x04    // any input timeout active -> motors commanded to 0 (OPEN_MODE)
#define CAN_STATUS_ESTOP          0x08    // e-stop active (latched until release frame)
#define CAN_STATUS_AUX_INPUT      0x10    // dual-inputs: auxiliary input (index 1) is active
#define CAN_STATUS_BAT_LOW        0x20    // battery below BAT_LVL1
#define CAN_STATUS_TEMP_WARN      0x40    // board temperature >= TEMP_WARNING

void    CAN_Init(void);
uint8_t CAN_Send(uint16_t stdId, const uint8_t *data, uint8_t len);
void    CAN_FeedbackTask(void);

#ifdef CONTROL_CAN
void    CAN_GetCommand(int16_t *in1, int16_t *in2);
uint8_t CAN_CommandReceived(void);
uint8_t CAN_EstopActive(void);
void    CAN_RX0_IRQ(void);
#endif

#endif

#endif
