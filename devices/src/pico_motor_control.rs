use serde::Serialize;

use crate::pi_pico::{PiPico, PicoDevice};

// Has to match the format in pi_pico/pi_pico/structs.h
#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable, Serialize)]
#[repr(C)] // Ensures a stable memory layout
pub struct MotorControllerCommand {
    pub motor_a_duty_cycle: f32,
    pub motor_b_duty_cycle: f32,
    pub enable_motors_bool: u32,
}

#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable, Serialize)]
#[repr(C)] // Ensures a stable memory layout
pub struct MotorControllerState {
    commanded_motor_a_duty_cycle: f32,
    commanded_motor_b_duty_cycle: f32,
    commanded_enable_motors_bool: u32,
}

pub struct MotorController;

impl PicoDevice for MotorController {
    type Telemetry = MotorControllerState;
    type Command = MotorControllerCommand;
}

pub type PicoMotorController = PiPico<MotorController>;
