// driver to read the digital compass (bno085) ic from a pi pico
// FIXME: rework the existing pi_pico.rs to act as a generic pi pico interface -> template that shi
// FIXME: need to add a calibration routine, enter it via command
pub struct Quaternion {
    pub w: f32,
    pub i: f32,
    pub j: f32,
    pub k: f32,
}

pub struct PicoDigitalCompassTelemetry {
    // Orientation of the sensor relative to the magnetic north pole
    // -> this needs to be combined with gps mangetic adjustment to get the real north pole
    real_orientation: Quaternion,
}
