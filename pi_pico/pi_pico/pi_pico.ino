#include "structs.h"
#include <cstring>
#include <limits>

constexpr int PwmPin = 15;

void setup() {
  pinMode(PwmPin, OUTPUT);
  analogWrite(PwmPin, 0);

  // This is running over the virtual port, baud rate is meaningless
  Serial.begin(115200);

  /* Not sure if this is actually reqiured?  */
  // while (!Serial) {
  // }

  Serial.println("Raspberry Pi Pico Serial Initialized!");
}

void loop() {
  // FIXME: Ingest the commands -> update local state
  static state_t state{};

  // FIXME: read command -> byte by byte?

  const uint8_t duty_cycle =
      state.commanded_motor_duty_cycle * std::numeric_limits<uint8_t>::max();
  analogWrite(PwmPin, duty_cycle);

  // TODO: measure ina's and shit
  Serial.write((uint8_t *)&state, sizeof(state_t));
}
