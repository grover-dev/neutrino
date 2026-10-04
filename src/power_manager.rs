use bms::DynessBmsData;
// FIXME: Mppt may be cooked... tbd if this can be cut
use devices::{bms, mppt, pi_pico};
use mppt::VictronData;
// FIXME: Add pi pico power info eventually...

// FIXME: how2datashare... build a blackboard by hand!
use db::{Database, Measurement, Record};

pub struct PowerManager {}

impl PowerManager {}
