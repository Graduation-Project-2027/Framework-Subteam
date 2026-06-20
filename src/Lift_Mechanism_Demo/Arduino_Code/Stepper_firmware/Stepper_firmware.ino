/* ============================================================================
 *  Lift Mechanism — Arduino UNO + GRBL/CNC Shield V3  (X & Y steppers synced)
 * ----------------------------------------------------------------------------
 *  Role in the system:
 *    Gazebo SIM ──Float64 height──> micro-ROS ──> ESP32 ──2 digital lines──> THIS
 *
 *  The ESP32 collapses the sim's continuous height into a binary intent and
 *  drives two lines into this board:
 *      PIN_LIFT held LOW  -> desired state = LIFTED
 *      PIN_DROP held LOW  -> desired state = DROPPED
 *      both HIGH          -> hold (no change)
 *
 *  The mechanism has NO middle position: it is either fully LIFTED or fully
 *  DROPPED. This firmware therefore:
 *      1. Blind-homes on boot (slams DOWN for HOMING_TIME_MS) and calls that 0.
 *      2. Latches the commanded state and, on a CHANGE, drives BOTH motors a
 *         fixed distance (LIFT_DISTANCE_MM, via STEPS_PER_MM) in perfect sync.
 *      3. Never repeats a move while a line is held — only state TRANSITIONS
 *         cause motion, so holding the sim setpoint is safe.
 * ==========================================================================*/

// --- CNC Shield V3 Pin Definitions ---
#define EN_PIN    8   // Stepper Enable Pin (Active LOW)
#define X_STEP    2   // X-Axis Step Pin
#define X_DIR     5   // X-Axis Direction Pin
#define Y_STEP    3   // Y-Axis Step Pin
#define Y_DIR     6   // Y-Axis Direction Pin

// --- Input Pins from ESP32 (active LOW) ---
#define PIN_DROP  A4
#define PIN_LIFT  A5

/* ============================ TUNE FOR YOUR RIG ============================ */
// Steps per millimetre of vertical travel:
//   STEPS_PER_MM = (motor_steps_per_rev * microstepping) / travel_mm_per_rev
//   e.g. 200 steps/rev * 16 microstep / 40 mm-per-rev (T8 lead, 4-start) = 80
const float STEPS_PER_MM = 80.0;

// Hard-coded real-world lift travel. The SIM lifts 0.20 m (20 cm); the bench
// rig usually moves less. Set this to the physical stroke of YOUR mechanism.
const float LIFT_DISTANCE_MM = 50.0;          // <-- the "say cm" distance

// Blind homing: slam DOWN continuously for this long, then call it zero.
const unsigned long HOMING_TIME_MS = 4000;    // 4 s

// Motion speed (trapezoidal ramp so a loaded lift doesn't stall/skip):
const unsigned int STEP_DELAY_MIN_US  = 300;  // cruise  (smaller = faster)
const unsigned int STEP_DELAY_MAX_US  = 1200; // ramp start/end (slow)
const unsigned int STEP_DELAY_HOME_US = 1000; // constant, gentle, for homing
const long         RAMP_STEPS         = 400;  // steps spent accel/decel

// Direction polarity — flip these if a motor runs the wrong way:
const bool DIR_UP   = HIGH;   // DIR level that RAISES the mechanism
const bool INVERT_Y = false;  // true if the Y motor is mounted mirrored to X
/* ========================================================================== */

const long LIFT_STEPS = (long)(LIFT_DISTANCE_MM * STEPS_PER_MM);

enum LiftState { DROPPED = 0, LIFTED = 1 };
LiftState currentState = DROPPED;   // set by homing in setup()

// ---------------------------------------------------------------------------
// Low-level helpers
// ---------------------------------------------------------------------------

// Point both axes in the same physical direction (honouring INVERT_Y).
void setDirection(bool up) {
  digitalWrite(X_DIR, up ? DIR_UP : !DIR_UP);
  bool yUp = INVERT_Y ? !up : up;
  digitalWrite(Y_DIR, yUp ? DIR_UP : !DIR_UP);
}

// One synchronous step pulse on BOTH axes. delayUs = half-period.
void pulseBoth(unsigned int delayUs) {
  digitalWrite(X_STEP, HIGH);
  digitalWrite(Y_STEP, HIGH);
  delayMicroseconds(delayUs);
  digitalWrite(X_STEP, LOW);
  digitalWrite(Y_STEP, LOW);
  delayMicroseconds(delayUs);
}

// Trapezoidal speed profile: slow at both ends, fast in the middle.
unsigned int rampDelay(long i, long total) {
  long distFromEnd = i < (total - 1 - i) ? i : (total - 1 - i);
  if (distFromEnd >= RAMP_STEPS) return STEP_DELAY_MIN_US;
  float t = (float)distFromEnd / (float)RAMP_STEPS;      // 0 .. 1
  return STEP_DELAY_MAX_US - (unsigned int)((STEP_DELAY_MAX_US - STEP_DELAY_MIN_US) * t);
}

// Fixed-distance synchronised move of both motors.
void moveLift(bool up) {
  setDirection(up);
  for (long i = 0; i < LIFT_STEPS; i++) {
    pulseBoth(rampDelay(i, LIFT_STEPS));
  }
}

// Blind home: drive DOWN at a constant gentle speed for HOMING_TIME_MS,
// crashing into the lower hard-stop. Skipped steps are expected & fine.
void blindHome() {
  Serial.println("Homing: slamming DOWN to find zero...");
  setDirection(false);                    // down
  unsigned long start = millis();
  while (millis() - start < HOMING_TIME_MS) {
    pulseBoth(STEP_DELAY_HOME_US);
  }
  currentState = DROPPED;                  // this position is now zero
  Serial.println("Homing complete. State = DROPPED (zero).");
}

// ---------------------------------------------------------------------------
// Read the two ESP32 lines and return the latched desired state.
//   PIN_LIFT low -> LIFTED ; PIN_DROP low -> DROPPED ; else keep last.
// ---------------------------------------------------------------------------
LiftState readDesiredState() {
  static LiftState desired = DROPPED;
  bool dropReq = (digitalRead(PIN_DROP) == LOW);
  bool liftReq = (digitalRead(PIN_LIFT) == LOW);

  if (liftReq && !dropReq)      desired = LIFTED;
  else if (dropReq && !liftReq) desired = DROPPED;
  // neither / both -> hold previous latched value
  return desired;
}

void setup() {
  Serial.begin(115200);

  pinMode(PIN_DROP, INPUT);
  pinMode(PIN_LIFT, INPUT);

  pinMode(EN_PIN, OUTPUT);
  pinMode(X_STEP, OUTPUT);
  pinMode(X_DIR, OUTPUT);
  pinMode(Y_STEP, OUTPUT);
  pinMode(Y_DIR, OUTPUT);

  digitalWrite(EN_PIN, LOW);               // enable drivers (active LOW)

  Serial.println("Arduino Lift Controller Ready.");
  Serial.print("LIFT_DISTANCE_MM="); Serial.print(LIFT_DISTANCE_MM);
  Serial.print("  STEPS_PER_MM=");   Serial.print(STEPS_PER_MM);
  Serial.print("  LIFT_STEPS=");     Serial.println(LIFT_STEPS);

  blindHome();                             // always home on boot
}

void loop() {
  LiftState desired = readDesiredState();

  if (desired != currentState) {
    if (desired == LIFTED) {
      Serial.println("Command: LIFT");
      moveLift(true);
    } else {
      Serial.println("Command: DROP");
      moveLift(false);
    }
    currentState = desired;                // reached it — won't repeat while held
  }
  // EN_PIN stays LOW so the motors hold position between moves.
}
