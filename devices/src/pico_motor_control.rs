// Has to match the format in pi_pico/pi_pico/structs.h
#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
#[repr(C)] // Ensures a stable memory layout
pub struct MotorControllerCommand {
    pub motor_a_duty_cycle: f32,
    pub motor_b_duty_cycle: f32,
}

#[derive(Default, Debug, Copy, Clone)]
#[repr(C)] // Ensures a stable memory layout
pub struct MotorControllerState {
    commanded_motor_a_duty_cycle: f32,
    commanded_motor_b_duty_cycle: f32,
}
