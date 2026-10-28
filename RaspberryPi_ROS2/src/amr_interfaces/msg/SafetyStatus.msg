# State of the e-stop controller (STM32F411 body board), CAN frame SAFETY_STATUS (0x011).
# It owns the relay that powers the hoverboard board and latches the e-stop.

uint8 CAUSE_NONE = 0
uint8 CAUSE_HOST_REQUEST = 1   # CAN ESTOP frame from the Pi (/estop service)
uint8 CAUSE_BUTTON = 2         # physical e-stop button
uint8 CAUSE_HOST_TIMEOUT = 3   # no wheel command from the Pi within the controller's watchdog

std_msgs/Header header
bool online                    # a SAFETY_STATUS frame arrived within status_timeout
bool estop_active              # latched e-stop
bool hoverboard_power          # relay closed, hoverboard board powered
bool button_pressed            # physical button currently pressed (blocks release)
uint8 cause                    # CAUSE_* of the active (or last) e-stop
uint8 heartbeat                # increments by one per frame
