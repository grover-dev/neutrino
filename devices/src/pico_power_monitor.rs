#[derive(Default, Debug, Copy, Clone)]
#[repr(C)] // Ensures a stable memory layout
pub struct VehiclePower {
    bus_voltage_v: f32,
    bus_current_ma: f32,
    motor_starboard_voltage_v: f32,
    motor_starboard_current_ma: f32,
    motor_port_voltage_v: f32,
    motor_port_current_ma: f32,
    starlink_voltage_v: f32,
    starlink_current_ma: f32,
}
