#pragma once

#include <cstdint>

struct command_t {
  // [0, 1.0]
  float motor_duty_cycle{};
};

struct state_t {
  // [0, 1.0]
  float commanded_motor_duty_cycle{};
  float motor_voltage_v{};
  float motor_current_ma{};
};
