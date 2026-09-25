#include "structs.h"

#include <INA226.h>
#include <PacketSerial.h>
#include <Servo.h>
#include <cmath>
#include <cstring>
#include <limits>

SLIPPacketSerial packetSerial;
command_t command{};
state_t state{};

Servo servo_controller;
// FIXME: check the addr
// INA226 INA(0x40);

// FIXME: check which ones work
constexpr int PwmPin = 15;

void setup() {
  pinMode(PwmPin, OUTPUT);
  // analogWrite(PwmPin, 0);
  servo_controller.attach(PwmPin);

  // 1. Set the PWM frequency to 50 Hz
  // analogWriteFreq(100);
  // analogWriteFreq(50);

  // 2. Increase resolution to 16-bit (0 to 65535) for precise control
  // analogWriteRange(65535);
  // analogWriteResolution(16);

  // This is running over the virtual port, baud rate is meaningless
  Serial.begin(115200);
  packetSerial.setStream(&Serial);

  // Set the function that will handle fully decoded packets
  packetSerial.setPacketHandler(&onPacketReceived);

  // Try to set up the INA226
  // Wire.begin();
  // if (!INA.begin() )
  // {
  //   Serial.println("could not connect. Fix and Reboot");
  //   while(1){}
  // }

  // INA.setMaxCurrentShunt(10, 0.002);
}

void loop() {
  /* Update state from commands */
  packetSerial.update();
  state.commanded_motor_duty_cycle = command.motor_duty_cycle;

  /* Netural = 1500 us, max forward is 2000 us, max reverse is 1000 us */
  int period = (state.commanded_motor_duty_cycle * 500) + 1500;
  servo_controller.writeMicroseconds(period);

  // analogWrite(PwmPin, duty_cycle);

  // state.motor_current_ma = INA.getCurrent_mA();
  // state.motor_voltage_v = INA.getBusVoltage() / 1000;

  // TODO: measure ina's and shit
  packetSerial.send((uint8_t *)&state, sizeof(state_t));

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
