#pragma once

#include <cstdint>

// Has to match the format in devices/src/pico_digital_compass.rs

// No default member initializers, they make this non-POD and gcc then ignores
// the packed attribute on any struct containing it. Zero it with {} instead.
struct quaternion_t {
  float w;
  float i;
  float j;
  float k;
};

// Packed to match #[repr(C, packed)] on the rust side (28 bytes, no padding)
struct __attribute__((packed)) digital_compass_telemetry_t {
  // Orientation of the sensor relative to magnetic north
  quaternion_t real_orientation{};
  // BNO08x report accuracy: 0 = unreliable, 1 = low, 2 = medium, 3 = high
  uint32_t calibration_state{};
  // Last received command values
  uint32_t start_calibration{};
  uint32_t flash_calibration{};
};

static_assert(sizeof(digital_compass_telemetry_t) == 28, "digital_compass_telemetry_t must match rust layout");

// Packed to match #[repr(C, packed)] on the rust side
struct __attribute__((packed)) digital_compass_command_t {
  // uint32_t instead of bool to match the rust Pod struct
  // Both act on a 0 -> nonzero transition
  // Enable dynamic calibration, figure-8 the compass after this
  uint32_t start_calibration{};
  // Save the calibration data to the BNO08x flash and end calibration
  uint32_t flash_calibration{};
};

static_assert(sizeof(digital_compass_command_t) == 8, "digital_compass_command_t must match rust layout");
