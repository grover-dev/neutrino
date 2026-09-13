#pragma once

#include <cstdint>

// good enough for now?
struct header_t {
  const uint32_t magic = 0xDEADBEEF;
};

struct terminator_t {
  const uint32_t magic = 0xCAFEBABE;
};

struct command_t {
  const header_t header{};
  // [0, 1.0]
  float motor_duty_cycle{};
  const terminator_t terminator{};
};

struct state_t {
  const header_t header{};
  // [0, 1.0]
  float commanded_motor_duty_cycle{};
  float motor_voltage_v{};
  float motor_current_a{};
  const terminator_t terminator{};
};
