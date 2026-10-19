/*
 * fuck it we ball
 */
// #![no_std]
use bms::DynessBmsData;
use devices::pico_digital_compass::DigitalCompassTelemetry;
use devices::pico_motor_control::MotorControllerState;
use devices::{bms, gps, pi_pico, pico_digital_compass};
use gps::GpsError;

use chrono::{DateTime, Utc};
use db::{Database, Measurement, Record};
use std::{net::UdpSocket, thread, time::Duration};

fn main() {
    let mut gps = gps::Gps::new("/dev/ttyUSB0");
    let mut digital_compass = pico_digital_compass::PicoDigitalCompass::new("/dev/ttyACM0");
    loop {
        match gps.update() {
            Ok(value) => println!("{:?}", value),
            Err(GpsError::NoData) => {}
            Err(GpsError::NoFix) => println!("gps: no fix"),
            Err(GpsError::ChecksumError) => println!("gps: checksum error"),
        }

        if let Some(compass_data) = digital_compass.read_telemetry() {
            // FIXME: This should convert the struct in json... ?
            // send compass_data out
            // db::Record::from_struct(Utc::now().timestamp(), &compass_data);
        } else {
            // mark compass data as invalid for controller?
        }

        thread::sleep(Duration::from_millis(100));
    }
}
