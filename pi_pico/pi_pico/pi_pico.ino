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

unsigned long last_command_time_ms{};
// If its been more than 1 second since the last command, safe yourself!
const unsigned long watchdog_expiration_duration_ms = 1'000;

Servo motor_a_controller;
Servo motor_b_controller;

// FIXME: check the addr
// INA226 INA(0x40);

constexpr int motor_a_pwm_pin = 15;
constexpr int motor_b_pwm_pin = 16;

void setup() {
  pinMode(motor_a_pwm_pin, OUTPUT);
  pinMode(motor_b_pwm_pin, OUTPUT);

  motor_a_controller.attach(motor_a_pwm_pin);
  motor_b_controller.attach(motor_b_pwm_pin);

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

  state.commanded_motor_a_duty_cycle = command.motor_a_duty_cycle;
  state.commanded_motor_b_duty_cycle = command.motor_b_duty_cycle;

  if (millis() - last_command_time_ms >= watchdog_expiration_duration_ms) {
    state.commanded_motor_a_duty_cycle = 0.0f;
    state.commanded_motor_b_duty_cycle = 0.0f;
  } else if (millis() < last_command_time_ms) {
    /* if we overflow -> reset the last command time */
    // TODO: CONFIRM THIS IS CHILL!
    last_command_time_ms = millis();
  }

  if (state.commanded_motor_a_duty_cycle > 1.0f ||
      state.commanded_motor_a_duty_cycle < -1.0f) {
    // guard against out of bound values
    state.commanded_motor_a_duty_cycle = 0.0f;
    state.commanded_motor_b_duty_cycle = 0.0f;
  }

  if (state.commanded_motor_b_duty_cycle > 1.0f ||
      state.commanded_motor_b_duty_cycle < -1.0f) {
    // guard against out of bound values
    state.commanded_motor_a_duty_cycle = 0.0f;
    state.commanded_motor_b_duty_cycle = 0.0f;
  }

  /* Netural = 1500 us, max forward is 2000 us, max reverse is 1000 us */
  const int period_a = (state.commanded_motor_a_duty_cycle * 500) + 1500;
  motor_a_controller.writeMicroseconds(period_a);

  const int period_b = (state.commanded_motor_b_duty_cycle * 500) + 1500;
  motor_b_controller.writeMicroseconds(period_b);

  // FIXME: bring in INA telemetry later!
  // state.motor_current_ma = INA.getCurrent_mA();
  // state.motor_voltage_v = INA.getBusVoltage() / 1000;

  // FIXME: add watchdog in case comms drop out
  // TODO: measure ina's and shit
  packetSerial.send((uint8_t *)&state, sizeof(state_t));

  /* Update at 10 Hz */
  delay(100);
}

void onPacketReceived(const uint8_t *buffer, size_t size) {
  // 'buffer' now contains your raw, clean data with SLIP encoding removed
  if (size == sizeof(command_t)) {
    command_t temp{};
    memcpy((uint8_t *)&temp, buffer, size);

    // FIXME: move this to somewhere else?
    if (!std::isfinite(temp.motor_a_duty_cycle) ||
        !std::isfinite(temp.motor_b_duty_cycle)) {
      return;
    }
    command = temp;

    // Record the time of the command!
    last_command_time_ms = millis();
  }
}

// FIXME: is this required? tbd... may use an external switch to supply power to
// pico instead. SEE uhubctl <- this can power cycle ports!
//
//  setup()
//  rp2040.wdt_begin(500);   // reboot if not fed within 500
// ms
// // loop()
// rp2040.wdt_reset();
