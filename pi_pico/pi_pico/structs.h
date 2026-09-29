#pragma once

#include <cstdint>

struct command_t {
  // [-1.0, 1.0]
  float motor_a_duty_cycle{};
  float motor_b_duty_cycle{};
};

struct state_t {
  // [-1.0, 1.0]
  float commanded_motor_a_duty_cycle{};
  float commanded_motor_b_duty_cycle{};
  // FIXME: These will eventually become a/b too!
  float motor_voltage_v{};
  float motor_current_ma{};
};
