// Reads the BNO085 rotation vector and streams it as SLIP encoded telemetry.
// Based on the Adafruit BNO08x demo
// https://learn.adafruit.com/adafruit-9-dof-orientation-imu-fusion-breakout-bno085/arduino
#include "structs.h"

#include <Adafruit_BNO08x.h>
#include <PacketSerial.h>
#include <cstring>

// For SPI mode, we need a CS pin
#define BNO08X_CS 10
#define BNO08X_INT 9

// For SPI mode, we also need a RESET
// #define BNO08X_RESET 5
// but not for I2C or UART
#define BNO08X_RESET -1

// NOTE: no plain Serial.print()s allowed, they would corrupt the SLIP stream
SLIPPacketSerial packetSerial;
digital_compass_telemetry_t telemetry{};
digital_compass_command_t command{};
// Previous command, used to only act on 0 -> nonzero transitions
digital_compass_command_t last_command{};
uint32_t calibrating_bool{};

Adafruit_BNO08x bno08x(BNO08X_RESET);
sh2_SensorValue_t sensorValue;

unsigned long last_send_time_ms{};
// Send telemetry at 10 Hz, matches the motor controller
const unsigned long send_period_ms = 100;

void setup(void) {
  // This is running over the virtual port, baud rate is meaningless
  Serial.begin(115200);
  packetSerial.setStream(&Serial);

  // Set the function that will handle fully decoded packets
  packetSerial.setPacketHandler(&onPacketReceived);

  // Try to initialize!
  if (!bno08x.begin_I2C()) {
    // if (!bno08x.begin_UART(&Serial1)) {  // Requires a device with > 300 byte
    // UART buffer! if (!bno08x.begin_SPI(BNO08X_CS, BNO08X_INT)) {
    // FIXME: report this failure in telemetry instead of going silent
    while (1) {
      delay(10);
    }
  }

  setReports();
  delay(100);
}

// Here is where you define the sensor outputs you want to receive
void setReports(void) {
  // FIXME: report failure in telemetry
  bno08x.enableReport(SH2_ROTATION_VECTOR);
}

// Turn on dynamic calibration for all sensors, figure-8 the compass while
// this is running
void startCalibration(void) {
  sh2_setCalConfig(SH2_CAL_ACCEL | SH2_CAL_GYRO | SH2_CAL_MAG);
}

// Persist the calibration data (DCD) to the BNO08x flash
void flashCalibration(void) {
  sh2_saveDcdNow();
}

void loop() {
  delay(10);

  /* Update command */
  packetSerial.update();

  if (bno08x.wasReset()) {
    setReports();
    // A reset drops the calibration config, re-apply it if mid calibration
    if (calibrating_bool > 0) {
      startCalibration();
    }
  }

  /* Act on calibration requests only on a 0 -> nonzero transition */
  if (command.start_calibration > 0 && last_command.start_calibration == 0) {
    startCalibration();
    calibrating_bool = 1;
  }
  if (command.flash_calibration > 0 && last_command.flash_calibration == 0) {
    flashCalibration();
    calibrating_bool = 0;
  }
  last_command = command;

  telemetry.start_calibration = command.start_calibration;
  telemetry.flash_calibration = command.flash_calibration;

  /* Update telemetry with the latest rotation vector, if there is one */
  if (bno08x.getSensorEvent(&sensorValue) &&
      sensorValue.sensorId == SH2_ROTATION_VECTOR) {
    telemetry.real_orientation.w = sensorValue.un.rotationVector.real;
    telemetry.real_orientation.i = sensorValue.un.rotationVector.i;
    telemetry.real_orientation.j = sensorValue.un.rotationVector.j;
    telemetry.real_orientation.k = sensorValue.un.rotationVector.k;
    // Lower 2 bits of status hold the report accuracy
    telemetry.calibration_state = sensorValue.status & 0x03;
  }

  /* Send at a fixed rate rather than per event so the host isn't flooded */
  if (millis() - last_send_time_ms >= send_period_ms) {
    last_send_time_ms = millis();
    packetSerial.send((uint8_t *)&telemetry, sizeof(digital_compass_telemetry_t));
  }
}

void onPacketReceived(const uint8_t *buffer, size_t size) {
  // 'buffer' now contains your raw, clean data with SLIP encoding removed
  if (size == sizeof(digital_compass_command_t)) {
    digital_compass_command_t temp{};
    memcpy((uint8_t *)&temp, buffer, size);
    command = temp;
  }
}
