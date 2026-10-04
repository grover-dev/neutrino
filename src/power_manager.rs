use bms::DynessBmsData;
// FIXME: Mppt may be cooked... tbd if this can be cut
use devices::{bms, mppt, pi_pico};
use mppt::VictronData;
// FIXME: Add pi pico power info eventually...

// FIXME: how2datashare... build a blackboard by hand!
use db::{Database, Measurement, Record};

pub struct PowerManager {}

// just feed power info to the power manager? tbd...

pub struct PowerData {
    // TBD, may restructure as optional structs...
    bms_data_valid: bool,
    bms_soc_percent: float,
    bms_charge_current_a: float,
    bms_discharge_current_a: float,

    // FIXME: This might just turn into solar array current
    mppt_data_valid: bool,
    mppt_on: bool, // If off -> probably load shed more aggressively..
    mppt_output_current_a: float,
    // TBD:
    // motor_current_data_valid : bool,
    // motor_current : float
}

pub struct PowerCommand {
    // TODO: Extend with more graceful/capable load shed
    motor_permitted_to_run: bool,
    // FIXME:
}

impl PowerManager {
    // FIXME: Make this an algorithm for now...
    pub fn step(power_data: &PowerData) -> PowerCommand {}
}
