# Telemetry of the hoverboard mainboard running Hoverboard_Firmware VARIANT_CAN
# (frames described in Hoverboard_Firmware/docs/hoverboard_can.dbc). It drives the
# left and right hub motors of the differential drive.
# Wheel values use the firmware convention: positive = robot moves forward.

uint8 ERROR_HALL_DISCONNECTED = 1
uint8 ERROR_HALL_SHORT = 2
uint8 ERROR_MOTOR_STALL = 4

std_msgs/Header header
string name                   # board role ("drive")
uint8 node_id                 # CAN_NODE_ID flashed into the board
bool online                   # a status frame arrived within status_timeout

# HB_MOTION (0x280 + node_id), 50 Hz
int16 rpm_left
int16 rpm_right
float32 current_left          # DC link current [A]
float32 current_right         # DC link current [A]

# HB_ODOM (0x180 + node_id), 50 Hz. Hall steps, ~90 per wheel revolution.
int32 ticks_left
int32 ticks_right

# HB_STATUS (0x380 + node_id), 10 Hz
float32 battery_voltage       # [V]
uint8 battery_percent         # [%]
int8 board_temperature        # [degC]
uint8 error_left              # bitmask of ERROR_* constants
uint8 error_right             # bitmask of ERROR_* constants
bool motors_enabled
bool cmd_timeout              # no CAN command within the firmware CAN_TIMEOUT
bool safe_state               # an input timed out, motors de-energized
bool estop_active             # hoverboard's own latched e-stop (braking); see SafetyStatus
bool aux_input
bool battery_low
bool temp_warning
uint8 can_error_counter       # max(TEC, REC); rising values = wiring/bit-timing problem
uint8 heartbeat               # increments by one per status frame
