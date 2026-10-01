// #include "structs.h"

#include <INA226.h>
#include <PacketSerial.h>
#include <cmath>
#include <cstring>
#include <limits>

SLIPPacketSerial packetSerial;
// command_t command{};
// state_t state{};

INA226 INA(0x40);

void setup() {
  // This is running over the virtual port, baud rate is meaningless
  Serial.begin(115200);
   while (!Serial) {
    ; // Do nothing and wait
  }
  Serial.println("Starting...");

  // packetSerial.setStream(&Serial);

  // Set the function that will handle fully decoded packets
  // packetSerial.setPacketHandler(&onPacketReceived);

  // Try to set up the INA226
  Wire.begin();
  Serial.println("Wire setup");
  if (!INA.begin()) {
    Serial.println("could not connect. Fix and Reboot");
    while (1) {
    }
  }

  INA.setMaxCurrentShunt(10, 0.002);
}

void loop() {
  /* Update state from commands */
  // packetSerial.update();
char buffer[50]; // Make sure the buffer is large enough for the entire string!
sprintf(buffer, "got measurement: %d mV", INA.getBusVoltage_mV());
  Serial.println(buffer);

  // FIXME: bring in INA telemetry later!
  // state.motor_current_ma = INA.getCurrent_mA();
  // state.motor_voltage_v = INA.getBusVoltage() / 1000;

  // FIXME: add watchdog in case comms drop out
  // TODO: measure ina's and shit
  // packetSerial.send((uint8_t *)&state, sizeof(state_t));

  /* Update at 10 Hz */
  delay(100);
}

void onPacketReceived(const uint8_t *buffer, size_t size) {
  // // 'buffer' now contains your raw, clean data with SLIP encoding removed
  // if (size == sizeof(command_t)) {
  //   command_t temp{};
  //   memcpy((uint8_t *)&temp, buffer, size);

  //   // Written as >= && <= so NaN (all comparisons false) is rejected too,
  //   along
  //   // with +/-inf and anything outside [-1, 1]
  //   auto in_range = [](float x) { return x >= -1.0f && x <= 1.0f; };
  //   if (!in_range(temp.motor_a_duty_cycle) ||
  //       !in_range(temp.motor_b_duty_cycle)) {
  //     // Drop the packet without feeding the watchdog
  //     return;
  //   }
  //   command = temp;

  //   // Record the time of the command!
  //   last_command_time_ms = millis();
  // }
}

// FIXME: is this required? tbd... may use an external switch to supply power to
// pico instead. SEE uhubctl <- this can power cycle ports!
//
//  setup()
//  rp2040.wdt_begin(500);   // reboot if not fed within 500
// ms
// // loop()
// rp2040.wdt_reset();
