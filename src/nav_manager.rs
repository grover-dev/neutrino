use derive_more::{Add, Sub};
use devices::gps::GpsData;
use devices::pico_digital_compass::Quaternion;
use std::f32::consts::PI;

use std::ops::Sub;

// FIXME: move this? tbd...
#[derive(PartialEq, Add, Sub)]
pub struct Position {
    latitude: f64,
    longitude: f64,
}

// Implement the Add trait for Point
// pub struct Position {
//     latitude: f64,
//     longitude: f64,
// }

pub struct NavManager {
    last_position: Position,
    last_heading: f64, // F
}

pub struct NavManagerInputCommand {
    // Target position combined with our course gives us our target course (trajectory planning)
    target_position: Position,
    motors_enabled: bool, // TBD if the nav manager needs to be aware...
}

pub struct NavManagerInputData {
    // Heading tells us what our motor force vectors are
    digital_compass_heading_mag_north: devices::pico_digital_compass::Quaternion,

    // FIXME: replace this with just raw gps data? tbd...

    // Speed and course give us our course vector
    // - FIXME: may calculate the true course using starlink data as a back up? tbd... requires integration over time, more complex algo... problem for later
    gps_speed_knots: f32,
    gps_course_degrees_true_opt: Option<f32>,
    gps_course_degrees_true: f32,
    gps_magnetic_correction_opt: Option<f32>, // FIXME: may to this by hand from lat/long?
    gps_magnetic_correction: f32,             // FIXME: may to this by hand from lat/long?
}

pub struct NavManagerTelem {
    // motor_a_duty_cycle: f32,
    // motor_b_duty_cycle: f32,
    // have this calculate a requested heading

    // FIXME: filter data?
    current_position: Position,
    // FIXME: I need to return a heading relative to the boat reference frame -> this is whats required to tell the motors what to do
    // - so then the job of the nav manmager is to figure out our current orientation globally using the digital compass + imu. gps
    // will give us the actual heading which should be fed into a closed loop controller which then adjusts our motors
    //   - expose this as a delta vector? 2d?
    target_position: Position,

    // mag north corrected to true north using GPS data
    digital_compass_heading_true_north: devices::pico_digital_compass::Quaternion,
    // current_velocity_vector: ... how to structure this... dLat dLong?
}

impl NavManager {
    pub fn step(input: &NavManagerInputData, command: &NavManagerInputCommand) -> NavManagerTelem {
        // let Some(gps) = gps_data else {
        //     // FIXME: zero out the motors? tbd... GPS data may drop out temporarily, dont want to kill it permanently
        //     return;
        // };
        let heading: f32 = Self::calculate_current_heading(
            &input.digital_compass_heading_mag_north,
            &input.gps_magnetic_correction,
        );

        // FIXME: Add logic to handle position + velocity calc here...
        // - can add deadreckoning if useful? tbd...

        return NavManagerTelem {
            current_position: Position {
                latitude: 0.0,
                longitude: 0.0,
            },
        };
    }

    fn calculate_current_heading(
        digital_compass_heading_mag_north: &devices::pico_digital_compass::Quaternion,
        true_north_correction: &f32,
    ) -> f32 {
        let r: f32 = digital_compass_heading_mag_north.w;
        let i: f32 = digital_compass_heading_mag_north.i;
        let j: f32 = digital_compass_heading_mag_north.j;
        let k: f32 = digital_compass_heading_mag_north.k;

        // FIXME: Double chekc this math...
        // Calculate yaw
        let siny_cosp: f32 = 2.0 * (r * k + i * j);
        let cosy_cosp: f32 = 1.0 - 2.0 * (j * j + k * k);
        let yaw: f32 = siny_cosp.atan2(cosy_cosp);

        // Convert to degrees and map to 0-360 clockwise
        let mut heading: f32 = -yaw * 180.0 / PI;
        if heading < 0.0 {
            heading += 360.0;
        }

        // FIXME: need to double check the reference frame here...
        heading += true_north_correction;
        return heading;
    }

    // FIXME: how to indicate course :hmm:
    fn calculate_target_course(
        current_position: Position,
        target_position: Position,
        gps_true_course: &f32,
    ) -> f32 {
        // FIXME: are ya fucking kidding me rust?
        let delta_position: Position = target_position - current_position;
        // FIXME:

        // FIXME:
        return 0.0;
    }
}
