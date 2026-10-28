# STM32 CAN bus embedded software

Firmware for the STM32 nodes on the LEVERAGE AMR's CAN bus, besides the hoverboard motor board
([../Hoverboard_Firmware](../Hoverboard_Firmware/README.md)).

| Folder | Contents |
|---|---|
| [Blackpill_CAN_Software](Blackpill_CAN_Software/README.md) | **BODY node**: WeAct Blackpill STM32F411CEU6 + MCP2515. E-stop authority (hoverboard power relay, e-stop button, Pi watchdog, `SAFETY_STATUS`), plus the **Blackpill Tool** to build, upload over SWD or DFU and use the debug terminal |
| [Docs](Docs) | Board references ([Blackpill pinout](Docs/Blackpill_Pinout.jpg)) |
| [Libraries](Libraries) | Shared libraries (empty for now; the MCP2515 driver lives in the project's `lib/`) |

Bus protocol: [hoverboard_can.dbc](../Hoverboard_Firmware/docs/hoverboard_can.dbc). Wiring, bitrate
and the e-stop behaviour on the robot:
[RaspberryPi_ROS2/docs/hardware.md](../RaspberryPi_ROS2/docs/hardware.md#2-can-bus).