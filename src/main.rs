/*
 * fuck it we ball
 */
// #![no_std]
use bms::DynessBmsData;
use devices::{bms, gps, mppt, pi_pico};
use gps::GpsError;
use mppt::VictronData;
use pi_pico::PiPicoState;

use std::{thread, time::Duration};

use db::{Database, Measurement, Record};

use crate::{boat_model::BoatModelOutput, nav_manager::Position};

mod boat_model;
mod nav_manager;
mod power_manager;

fn main() {
    // let mut gps = gps::Gps::new("/dev/ttyUSB0");
    // loop {
    //     match gps.update() {
    //         Ok(value) => println!("{:?}", value),
    //         Err(GpsError::NoData) => {}
    //         Err(GpsError::NoFix) => println!("gps: no fix"),
    //         Err(GpsError::ChecksumError) => println!("gps: checksum error"),
    //     }
    // }
    let mut boat_model = boat_model::BoatModel::new(100.0, 0.1, 0.1, 1.0, 0.2, 0.2);
    let mut nav_manager = nav_manager::NavManager::new(1.0, 0.01, 1.0);
    let mut input = nav_manager::NavManagerInputData::default();
    let mut command = nav_manager::NavManagerInputCommand::default();
    let mut nav_telem = nav_manager::NavManagerTelem::default();

    command.target_position = Position {
        latitude: 1.0,
        longitude: 1.0,
    };

    command.speed_setpoint_knots = 4.0;

    loop {
        nav_telem = nav_manager.step(&input, &command);

        let model_output: BoatModelOutput =
            boat_model.step(nav_telem.starboard_duty, nav_telem.port_duty);

        input.digital_compass_heading_mag_north = model_output.heading_quaternion;
        input.gps_speed_knots = model_output.speed_knots as f32;
        input.current_position = model_output.new_position;
        input.gps_course_degrees_true = model_output.new_heading_true as f32;
        input.gps_magnetic_correction = 0.0; // fixme: update!
        // println!("{:#?}", nav_telem);

        thread::sleep(Duration::from_millis(100));
    }

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

    // let mut pi_pico: pi_pico::PiPico = pi_pico::PiPico::new("/dev/ttyACM0");

    // // Duty cycle can be positive or negative [-1.0, 1.0]
    // // - negative indicates reverse
    // let mut duty_cycle: f32 = 0.0;

    // // Read duty cycles from stdin on a separate thread so polling isn't blocked
    // let (tx, rx) = std::sync::mpsc::channel::<f32>();
    // thread::spawn(move || {
    //     for line in std::io::stdin().lines() {
    //         match line.unwrap().trim().parse::<f32>() {
    //             Ok(value) => tx.send(value.clamp(-1.0, 1.0)).unwrap(),
    //             Err(_) => println!("expected a number in [-1.0, 1.0]"),
    //         }
    //     }
    // });

    // loop {
    //     let data: Option<PiPicoState> = pi_pico.poll();

    //     // if let Some(value) = data {
    //     //     println!("{:?}", value);
    //     // }

    //     let old_duty_cycle = duty_cycle;
    //     if let Ok(value) = rx.try_recv() {
    //         duty_cycle = value;
    //     }

    //     // fixme: jank in a watchdog failure to test!

    //     if duty_cycle != old_duty_cycle {
    //         let command = pi_pico::PiPicoCommand {
    //             motor_a_duty_cycle: duty_cycle,
    //             motor_b_duty_cycle: duty_cycle,
    //         };

    //         pi_pico.command_duty_cycle(&command);
    //     }

    //     thread::sleep(Duration::from_millis(100));
    // }
}
