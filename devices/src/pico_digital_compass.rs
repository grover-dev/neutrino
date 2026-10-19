// driver to read the digital compass (bno085) ic from a pi pico
// FIXME: rework the existing pi_pico.rs to act as a generic pi pico interface -> template that shi
// FIXME: need to add a calibration routine, enter it via command
#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
#[repr(C)] // Ensures a stable memory layout
pub struct Quaternion {
    pub w: f32,
    pub i: f32,
    pub j: f32,
    pub k: f32,
}

// enum DigitalCompassState
// {
//     Running,

// }

// FIXME: Need to add a command struct...

#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
#[repr(C, packed)] // Ensures a stable memory layout
pub struct DigitalCompassTelemetry {
    // Orientation of the sensor relative to the magnetic north pole
    // -> this needs to be combined with gps mangetic adjustment to get the real north pole
    real_orientation: Quaternion,
    calibration_state: u32, // FIXME: replace with an enum?
    // Last received command values
    start_calibration: u32,
    flash_calibration: u32,
}

#[derive(Default, Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
#[repr(C, packed)] // Ensures a stable memory layout
pub struct DigitalCompassCommand {
    start_calibration: u32,
    flash_calibration: u32,
}
