use derive_more::{Add, Sub};
use devices::gps::GpsData;
use devices::pico_digital_compass::Quaternion;
use std::f64::consts::PI;

use std::ops::Sub;
const KM_TO_NM: f64 = 0.539957;
// FIXME:

// FIXME: move this? tbd...
#[derive(PartialEq, Add, Sub, Clone, Copy)]
pub struct Position {
    latitude: f64,
    longitude: f64,
}

// FIXME: Move this to a different crate
pub struct pid {
    kp: f64,
    ki: f64,
    integral: f64,
    integral_windup_limit: f64,
}

impl pid {
    pub fn new(kp: f64, ki: f64, integral_windup_limit: f64) -> Self {
        Self {
            kp: kp,
            ki: ki,
            integral: 0.0,
            integral_windup_limit: integral_windup_limit,
        }
    }

    pub fn step(&mut self, error: f64) -> f64 {
        let p = error * self.kp;
        self.integral += (error * self.ki)
            .min(self.integral_windup_limit)
            .max(-self.integral_windup_limit);
        let i = self.integral;

        let output = p + i;
        return output;
    }
}

pub struct NavManager {
    // last_position: Position,
    // last_heading: f64, // F
    pid_velocity: pid,
}

pub struct NavManagerInputCommand {
    // Target position combined with our course gives us our target course (trajectory planning)
    target_position: Position,
    motors_enabled: bool, // TBD if the nav manager needs to be aware...
}

pub struct NavManagerInputData {
    // Heading tells us what our motor force vectors are
    digital_compass_heading_mag_north: devices::pico_digital_compass::Quaternion,

    // FIXME: replace this with just raw gps data? tbd..
    current_position: Position,

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
    // boat pointing
    current_heading: f64,
    // boat moving
    current_bearing: f64,

    // where we want the boat moving given our current position
    target_bearing: f64,

    // FIXME: I need to return a heading relative to the boat reference frame -> this is whats required to tell the motors what to do
    // - so then the job of the nav manmager is to figure out our current orientation globally using the digital compass + imu. gps
    // will give us the actual heading which should be fed into a closed loop controller which then adjusts our motors
    //   - expose this as a delta vector? 2d?
    target_position: Position,
    distance_to_target_nm: f64, // FIXME: Standardize on nautical miles vs km... probably do natuical miles...

    starboard_differential_duty: f64,
    port_differential_duty: f64,

    starboard_duty: f64,
    port_duty: f64,
}

// FIXME: will need to extend with automatic collision avoidance
// - need to also test the shit out of this...
// TODO: Extend with dead reckoning too
impl NavManager {
    pub fn new(kp: f64, ki: f64, integral_windup_limit: f64) -> Self {
        return Self {
            pid_velocity: pid::new(kp, ki, integral_windup_limit),
        };
    }

    pub fn step(
        &mut self,
        input: &NavManagerInputData,
        command: &NavManagerInputCommand,
    ) -> NavManagerTelem {
        // let Some(gps) = gps_data else {
        //     // FIXME: zero out the motors? tbd... GPS data may drop out temporarily, dont want to kill it permanently
        //     return;
        // };new(kp: f64, ki: f64, integral_windup_limit: f64) -> Self {
        //     return Self {
        //         pid_steer: pid::new(kp, ki, integral_windup_limit),
        //     };
        // }
        let current_heading: f32 = Self::calculate_current_heading(
            &input.digital_compass_heading_mag_north,
            &input.gps_magnetic_correction,
        );
        let distance_to_target_nm = Self::calculate_great_circle_distance_nm(
            &input.current_position,
            &command.target_position,
        );

        // FIXME: Standardize on 32 bit floats instead fo spamming f64s...
        let target_bearing: f64 =
            Self::calculate_target_bearing(&input.current_position, &command.target_position);

        // FIXME: This will be optional -> account for it...
        // Can also calculate using data. unreliable if we are slow moving (ues position info with a filter)
        let current_bearing: f64 = input.gps_course_degrees_true as f64;
        // FIXME: Add logic to handle position + velocity calc here...
        // - can add deadreckoning if useful? tbd...

        // FIXME: This assumes bearing = heading -> if hti si s not the case the logic needs to change
        // more positive = need to turn more clockwise
        let mut delta_bearing: f64 = target_bearing - current_bearing;

        // jank but whatever, rework later
        if delta_bearing >= 180.0 {
            /* Normalize back to +/- 180 */
            delta_bearing -= 360.0;
        } else if delta_bearing <= -180.0 {
            delta_bearing += 360.0;
        }

        /* Clockwise error = turn to starboard, otherwise turn to port */
        /* PID could probably help make this more aggressive, with small error this will only gently turn more to starboard.
         * Probably fine over long distances but tbd... */
        // rnage of [-1.0, 1.0], where negative is a reverse. Probably fine but need to test.
        let starboard_differential_duty = -(delta_bearing / 180.0);
        let port_differential_duty = (delta_bearing / 180.0);

        /*
         * Time to calculate the velocity vector! subject to power limits...
         */

        // let force = self.pid_velocity.step(delta_bearing);
        // FIXME: Now add a basic pid to

        // now feed the delta bearing into a PID? need to shit out correction angle -> apply to thrust from the engines?
        // i feel confident that the current heading is important but i have not figured out how to apply it exactly yet
        // FIXME: for now assume bearing ~ heading, this will not be true but should be a decent approximation for now

        return NavManagerTelem {
            current_position: input.current_position,
            current_heading: current_heading as f64,
            current_bearing: current_bearing,
            target_bearing: target_bearing,
            target_position: command.target_position,
            distance_to_target_nm: distance_to_target_nm,
            starboard_differential_duty: starboard_differential_duty,
            port_differential_duty: port_differential_duty,
            starboard_duty: starboard_duty,
            port_duty: port_duty,
        };
    }
    // formulas from https://www.movable-type.co.uk/scripts/latlong.html

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
        let mut heading: f32 = -yaw * 180.0 / (PI as f32);
        if heading < 0.0 {
            heading += 360.0;
        }

        // FIXME: need to double check the reference frame here...
        heading += true_north_correction;
        return heading;
        // new(kp: f64, ki: f64, integral_windup_limit: f64) -> Self {
        //         return Self {
        //             pid_steer: pid::new(kp, ki, integral_windup_limit),
        //         };
        //     }
    }

    // FIXME: Use this to detect when we have arrived at our target
    fn calculate_great_circle_distance_nm(from: &Position, to: &Position) -> f64 {
        let delta_lat_rad = (to.latitude - from.latitude) * PI / 180.0;
        let delta_long_rad = (to.longitude - from.longitude) * PI / 180.0;

        let lat_from_rad = from.latitude * PI / 180.0;
        let lat_to_rad = to.latitude * PI / 180.0;

        // where: 	φ is latitude, λ is longitude, R is earth’s radius (mean radius = 6,371km);
        // note that angles need to be in radians to pass to trig functions!
        // formula: 	a = sin²(Δφ/2) + cos φ1 ⋅ cos φ2 ⋅ sin²(Δλ/2)
        let a = (delta_lat_rad / 2.0).sin().powi(2)
            + lat_from_rad.cos() * lat_to_rad.cos() * ((delta_long_rad / 2.0).sin().powi(2));
        // float error can push a just past 1.0 for near-antipodal points, making √(1−a) NaN
        let a = a.min(1.0);

        // c = 2 ⋅ atan2( √a, √(1−a) )
        let c = 2.0 * (a.sqrt().atan2((1.0 - a).sqrt()));
        let r = 6371.0 * KM_TO_NM;

        // d = R ⋅ c
        return r * c;
    }

    // Initial great-circle bearing (forward azimuth) from current to target (relative to true north), in degrees
    // clockwise from true north [0, 360). Bearing drifts along a great circle, so recompute as we move.
    // formula: θ = atan2( sin Δλ ⋅ cos φ2 , cos φ1 ⋅ sin φ2 − sin φ1 ⋅ cos φ2 ⋅ cos Δλ )
    fn calculate_target_bearing(current_position: &Position, target_position: &Position) -> f64 {
        let delta_long_rad = (target_position.longitude - current_position.longitude) * PI / 180.0;

        let lat_a_rad = current_position.latitude * PI / 180.0;
        let lat_b_rad = target_position.latitude * PI / 180.0;

        let y = delta_long_rad.sin() * lat_b_rad.cos();
        let x = lat_a_rad.cos() * lat_b_rad.sin()
            - lat_a_rad.sin() * lat_b_rad.cos() * delta_long_rad.cos();
        let theta = y.atan2(x);

        // atan2 returns -180..180, normalise to a 0..360 compass bearing
        return (theta * 180.0 / PI + 360.0) % 360.0;
    }
}
