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

use db::{Database, Measurement, Record};

use chrono::Utc;

fn main() {
    // // println!("Hello, world!");
    // // // let mut mppt: mppt::VictronMppt =
    // //     mppt::VictronMppt::new("/dev/ttyUSB0", mppt::BaudRate::_19200);

    // let mut bms: bms::DynessBms = bms::DynessBms::new("127.0.0.1:9000");

    // // FIXME: undo this...
    // let mut db = Database::new("telemetry.db").unwrap();

    // loop {
    //     // let data: Option<VictronData> = mppt.poll();

    //     // if let Some(value) = data {
    //     //     println!("{:?}", value);
    //     // }
    //     // }

    //     // loop {
    //     let data: Option<DynessBmsData> = bms.poll();

    //     if let Some(value) = data {
    //         println!("{:?}", value);

    //         // FIXME: Probably rework this to be columnar data instead? tbd -> easier to ingest?
    //         // inefficient? very much so, but should be good nuff for now
    //         let record = db::Record::from_struct(Utc::now().timestamp(), &value);
    //         db.insert(&record).unwrap();
    //     }
    // }

    let mut pi_pico: pi_pico::PiPico = pi_pico::PiPico::new("/dev/ttyACM0");

    // Duty cycle can be positive or negative [-1.0, 1.0]
    // - negative indicates reverse
    let mut duty_cycle: f32 = 0.0;

    // Read duty cycles from stdin on a separate thread so polling isn't blocked
    let (tx, rx) = std::sync::mpsc::channel::<f32>();
    thread::spawn(move || {
        for line in std::io::stdin().lines() {
            match line.unwrap().trim().parse::<f32>() {
                Ok(value) => tx.send(value.clamp(-1.0, 1.0)).unwrap(),
                Err(_) => println!("expected a number in [-1.0, 1.0]"),
            }
        }
    });

    loop {
        let data: Option<PiPicoState> = pi_pico.poll();

        // if let Some(value) = data {
        //     println!("{:?}", value);
        // }

        let old_duty_cycle = duty_cycle;
        if let Ok(value) = rx.try_recv() {
            duty_cycle = value;
        }

        // fixme: jank in a watchdog failure to test!

        if duty_cycle != old_duty_cycle {
            let command = pi_pico::PiPicoCommand {
                motor_a_duty_cycle: duty_cycle,
                motor_b_duty_cycle: duty_cycle,
            };

            pi_pico.command_duty_cycle(&command);
        }

        thread::sleep(Duration::from_millis(100));
    }
}
