use crate::nav_manager::Position;
use devices::pico_digital_compass::Quaternion;

const MPS_TO_KNOTS: f64 = 1.943844;

#[derive(Default, Debug)]
pub struct BoatModel {
    rotation_velocity: f64,
    rotation: f64,

    position: Position,
    forward_velocity: f64,

    mass_kg: f64,
    dt: f64,
    beam_m: f64,
    length_m: f64,
    // quadratic surge drag, N/(m/s)^2
    drag_coeff: f64,
    // linear yaw damping, N*m/(rad/s)
    rot_drag_coeff: f64,
}

pub struct BoatModelOutput {
    pub new_position: Position,
    pub new_heading_true: f64,
    // speed through the water along the heading
    pub speed_knots: f64,
    // heading as a pure yaw quaternion, in the compass frame nav_manager expects
    pub heading_quaternion: Quaternion,
}

impl BoatModel {
    pub fn new(
        mass_kg: f64,
        dt: f64,
        beam_m: f64,
        length_m: f64,
        drag_coeff: f64,
        rot_drag_coeff: f64,
    ) -> Self {
        let mut model = BoatModel::default();
        model.mass_kg = mass_kg;
        model.dt = dt;
        model.beam_m = beam_m;
        model.length_m = length_m;
        model.drag_coeff = drag_coeff;
        model.rot_drag_coeff = rot_drag_coeff;
        return model;
    }

    pub fn step(&mut self, starboard_duty: f64, port_duty: f64) -> BoatModelOutput {
        // assume x amount of force...
        let force_max = 160.0 as f64; // rougly max force from the props

        // FIXME: standardize on names this sucks
        let force_starboard = force_max * starboard_duty;
        let force_port = force_max * port_duty;

        // props sit at the hull sides, half the beam off the centerline
        let torque_starboard = force_starboard * self.beam_m / 2.0;
        let torque_port = force_port * self.beam_m / 2.0;

        // torque from starboard is positive -> counterclockwise (bow swings to port)
        let net_torque = torque_starboard - torque_port;
        let net_force = force_starboard + force_port;

        // drag: quadratic for surge, linear for yaw
        let drag = self.drag_coeff * self.forward_velocity * self.forward_velocity.abs();
        let rot_drag = self.rot_drag_coeff * self.rotation_velocity;

        let net_forward_acceleration = (net_force - drag) / self.mass_kg;

        // approximating as rectangle, m*(b^2 + h^2)/12 = i
        let rotational_inertia =
            self.mass_kg * (self.beam_m.powi(2) + self.length_m.powi(2)) / 12.0;
        // rads/s^2
        let net_rotation_acceleration = (net_torque - rot_drag) / rotational_inertia;

        self.forward_velocity += net_forward_acceleration * self.dt;

        self.rotation_velocity += net_rotation_acceleration * self.dt;
        self.rotation += self.rotation_velocity * self.dt;

        // FIXME: approximate meters per degree at the equator
        let meters_per_deg_latitude: f64 = 110_567.0;
        // let meters_per_deg_longitude : f64= 111_319.0;
        // approximate degrees; rotation 0 is east, positive is CCW
        let distance_m = self.forward_velocity * self.dt;
        let latitude =
            self.position.latitude + distance_m * self.rotation.sin() / meters_per_deg_latitude;
        let longitude =
            self.position.longitude + distance_m * self.rotation.cos() / meters_per_deg_latitude;

        self.position = Position {
            latitude,
            longitude,
        };

        // rads to degrees, roughly... east is 90 degrees, north is 0, west is 270, south is 180 -> need to rotate
        let heading_true = (90.0 - self.rotation.to_degrees()).rem_euclid(360.0);

        // nav_manager decodes heading = -yaw, so yaw = -heading. rotation about z only:
        // w = cos(yaw/2), k = sin(yaw/2)
        let half_yaw = -heading_true.to_radians() / 2.0;
        let heading_quaternion = Quaternion {
            w: half_yaw.cos() as f32,
            i: 0.0,
            j: 0.0,
            k: half_yaw.sin() as f32,
        };

        return BoatModelOutput {
            new_position: self.position,
            new_heading_true: heading_true,
            speed_knots: self.forward_velocity * MPS_TO_KNOTS,
            heading_quaternion,
        };
        // propagate
    }
}
