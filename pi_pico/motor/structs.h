#pragma once

#include <cstdint>

struct command_t {
  // [-1.0, 1.0]
  float motor_a_duty_cycle{};
  float motor_b_duty_cycle{};
  uint32_t enable_motors_bool{};
  // fucking no bools in pod piece of shit
  // uint32_t motor_a_enabled_bool{};
  // uint32_t motor_b_enabled_bool{};
};

struct state_t {
  // [-1.0, 1.0]
  float commanded_motor_a_duty_cycle{};
  float commanded_motor_b_duty_cycle{};
  uint32_t commanded_enable_motors_bool{};
};
