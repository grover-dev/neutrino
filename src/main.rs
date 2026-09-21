/*
 * fuck it we ball
 */
// #![no_std]
mod mppt;
use mppt::VictronData;

mod bms;
use bms::DynessBmsData;

use crate::pi_pico::PiPicoState;

mod pi_pico;
// use
use std::{thread, time::Duration};

mod database;
use database::{Database, Measurement, Record};

use chrono::Utc;

fn main() {
    // println!("Hello, world!");
    // // let mut mppt: mppt::VictronMppt =
    //     mppt::VictronMppt::new("/dev/ttyUSB0", mppt::BaudRate::_19200);

    let mut bms: bms::DynessBms = bms::DynessBms::new("127.0.0.1:9000");

    // FIXME: undo this...
    let mut db = Database::new("telemetry.db").unwrap();

    loop {
        // let data: Option<VictronData> = mppt.poll();

        // if let Some(value) = data {
        //     println!("{:?}", value);
        // }
        // }

        // loop {
        let data: Option<DynessBmsData> = bms.poll();

        if let Some(value) = data {
            println!("{:?}", value);

            let record = database::Record::from_struct(Utc::now(), &value);

            // good nuff
            db.insert(&record).unwrap();
        }
    }

    // let mut pi_pico: pi_pico::PiPico = pi_pico::PiPico::new("/dev/ttyACM0");

    // let mut duty_cycle: f32 = 0.0;
    // loop {
    //     let data: Option<PiPicoState> = pi_pico.poll();

    //     if let Some(value) = data {
    //         println!("{:?}", value);
    //     }

    //     pi_pico.command_duty_cycle(duty_cycle);

    //     duty_cycle += 0.1;
    //     if duty_cycle > 1.0 {
    //         duty_cycle = 0.0;
    //     }

    //     thread::sleep(Duration::from_millis(100));
    // }
}
