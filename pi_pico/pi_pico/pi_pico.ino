#include "structs.h"

#include <PacketSerial.h>
#include <cmath>
#include <cstring>
#include <limits>

SLIPPacketSerial packetSerial;
command_t command{};
state_t state{};

constexpr int PwmPin = 15;

void setup() {
  pinMode(PwmPin, OUTPUT);
  analogWrite(PwmPin, 0);

  // This is running over the virtual port, baud rate is meaningless
  Serial.begin(115200);
  packetSerial.setStream(&Serial);

  // Set the function that will handle fully decoded packets
  packetSerial.setPacketHandler(&onPacketReceived);

  Serial.println("Raspberry Pi Pico Serial Initialized!");
}

void loop() {
  /* Update state from commands */
  packetSerial.update();
  state.commanded_motor_duty_cycle = command.motor_duty_cycle;

  const uint8_t duty_cycle =
      state.commanded_motor_duty_cycle * std::numeric_limits<uint8_t>::max();
  analogWrite(PwmPin, duty_cycle);

  // TODO: measure ina's and shit
  Serial.write((uint8_t *)&state, sizeof(state_t));

  /* Update at 10 Hz */
  delay(100);
}

void onPacketReceived(const uint8_t *buffer, size_t size) {
  // 'buffer' now contains your raw, clean data with SLIP encoding removed
  if (size == sizeof(command_t)) {
    float temp = 0.0f;
    memcpy((uint8_t *)&temp, buffer, size);

    if (!std::isfinite(temp)) {
      return;
    }
    command.motor_duty_cycle = temp;
  }
}
