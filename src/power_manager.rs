pub struct PowerManager {
    last_motor_permitted_state: bool,
    load_shed_threshold: i32,
    load_shed_hysteresis: i32,
}

// just feed power info to the power manager? tbd...

pub struct PowerData {
    // TBD, may restructure as optional structs...
    bms_data_valid: bool,
    bms_soc_percent: f32,
    bms_charge_current_a: f32,
    bms_discharge_current_a: f32,

    // FIXME: This might just turn into solar array current
    mppt_data_valid: bool,
    mppt_on: bool, // If off -> probably load shed more aggressively..
    mppt_output_current_a: f32,
    // FIXME: add this
    // nauvionics_bus_voltage_valid: bool,
    // nauvionics_bus_voltage_v: f32,
    // FIXME: Probably should estimate how much current is NET being added in

    // TBD:
    // motor_current_data_valid : bool,
    // motor_current : f32
}

pub struct PowerCommand {
    // TODO: Extend with more graceful/capable load shed
    motor_permitted_to_run: bool,
    // FIXME:
}

impl PowerManager {
    pub fn new<const LOAD_SHED_THRESHOLD: i32, const LOAD_SHED_RECOVERY_HYSTERESIS: i32>() -> Self {
        const {
            assert!(
                LOAD_SHED_THRESHOLD <= 100 && LOAD_SHED_THRESHOLD >= 0,
                "Threshold must be [0,100]"
            );
            assert!(
                LOAD_SHED_RECOVERY_HYSTERESIS <= 100 && LOAD_SHED_RECOVERY_HYSTERESIS >= 0,
                "hysteresis must be [0,100]"
            );
        }

        return Self {
            last_motor_permitted_state: false,
            load_shed_threshold: LOAD_SHED_THRESHOLD,
            load_shed_hysteresis: LOAD_SHED_RECOVERY_HYSTERESIS,
        };
    }

    // FIXME: Make this an algorithm for now...
    pub fn step(&mut self, power_data: &PowerData) -> PowerCommand {
        // BMS data is our primary source, if its not available fall back to bus voltage
        // - if neither are available... what to do? safe the motors? add some estimator model? idk
        let mut command = PowerCommand {
            motor_permitted_to_run: false,
        };

        // Only run the motor if we have valid data and we are above a reasonable thresohld for battery charge
        if power_data.bms_data_valid {
            if self.last_motor_permitted_state == false
                && power_data.bms_soc_percent as i32
                    >= (self.load_shed_threshold + self.load_shed_hysteresis)
            {
                // either we recovered from UV or we're just booting, either way safe to run
                command.motor_permitted_to_run = true;
            } else if self.last_motor_permitted_state == true
                && power_data.bms_soc_percent as i32 <= self.load_shed_threshold
            {
                // dropping into load shed, turn off the motors
                command.motor_permitted_to_run = false;
            }
        }

        self.last_motor_permitted_state = command.motor_permitted_to_run;
        return command;
    }
}
