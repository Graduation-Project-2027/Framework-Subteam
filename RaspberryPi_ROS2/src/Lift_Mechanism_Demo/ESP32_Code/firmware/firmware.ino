/* ============================================================================
 *  Lift Mechanism — ESP32 bridge  (micro-ROS  <->  Arduino UNO)
 * ----------------------------------------------------------------------------
 *  Subscribes to the SAME topic the Gazebo SIM commands the lift on:
 *      /lift_joint/cmd_pos   std_msgs/Float64   (height in METRES)
 *
 *  The real rig has no middle position, so the continuous sim height is
 *  collapsed to a binary intent and pushed to the Arduino over two lines:
 *      PIN_OUT_LIFT LOW  -> Arduino lifts
 *      PIN_OUT_DROP LOW  -> Arduino drops
 *      both HIGH         -> hold (only before the first command)
 *
 *  Two physical buttons (active-LOW to GND) act as a manual override and
 *  always work — the most recent command (ROS or button) wins and is latched.
 * ==========================================================================*/

#include <Arduino.h>
#include "MicroRosConfig.h"

// Output pins (to Arduino inputs A4/A5)
#define PIN_OUT_DROP 33
#define PIN_OUT_LIFT 32

// Physical override buttons (to GND)
#define SW_DROP 25
#define SW_LIFT 22

// Binarise the commanded height: anything above the MIDDLE of the stroke is a
// lift, at/near 0 is a drop.  Kept well below the sim lift target (0.05 m) so a
// mission commanding exactly 0.05 m still reads as LIFT (0.05 > 0.025).
const float LIFT_THRESHOLD_M = 0.025f;

enum LiftIntent { DROP = 0, LIFT = 1 };
volatile LiftIntent desired = DROP;     // latched; default dropped on boot

void subscription_callback(const void *msgin) {
  const std_msgs__msg__Float64 *m = (const std_msgs__msg__Float64 *)msgin;
  desired = (m->data > LIFT_THRESHOLD_M) ? LIFT : DROP;
  Serial.print("SIM height: ");
  Serial.print(m->data, 3);
  Serial.println(desired == LIFT ? " m -> LIFT" : " m -> DROP");
}

void setup() {
  Serial.begin(115200);
  delay(1000);

  pinMode(SW_DROP, INPUT_PULLUP);
  pinMode(SW_LIFT, INPUT_PULLUP);

  pinMode(PIN_OUT_DROP, OUTPUT);
  pinMode(PIN_OUT_LIFT, OUTPUT);
  digitalWrite(PIN_OUT_DROP, HIGH);       // idle: no command
  digitalWrite(PIN_OUT_LIFT, HIGH);

  setup_microros("esp32_lift_node", "/lift_joint/cmd_pos", subscription_callback);
}

void loop() {
  spin_microros();

  // Manual override — latest press wins, latched until next command.
  if (digitalRead(SW_LIFT) == LOW) desired = LIFT;
  if (digitalRead(SW_DROP) == LOW) desired = DROP;

  // Drive the latched intent onto the two lines (always assert exactly one).
  if (desired == LIFT) {
    digitalWrite(PIN_OUT_LIFT, LOW);
    digitalWrite(PIN_OUT_DROP, HIGH);
  } else {
    digitalWrite(PIN_OUT_DROP, LOW);
    digitalWrite(PIN_OUT_LIFT, HIGH);
  }

  delay(10);
}
