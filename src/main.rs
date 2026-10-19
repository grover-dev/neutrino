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

// FIXME: split this off into a separate file eventually
use serde_json::Value;
// i want to create a trait that will append a timestamp field + add a to json method for any given struct?
// - or this could be a free floating function, tbd... former if telemetry becomes a special type

fn send_telem<T>(data: T, socket: &UdpSocket) {}

fn main() {
    let mut gps = gps::Gps::new("/dev/ttyUSB0");
    let mut digital_compass = pico_digital_compass::PicoDigitalCompass::new("/dev/ttyACM0");

    // FIXME: double check these...
    let socket = UdpSocket::bind("127.0.0.1:34254")?;
    socket.connect("127.0.0.1:34254")?;

    loop {
        // read_commands()

        // read()
        let gps_data = gps.update();
        let compass_data = digital_compass.read_telemetry();
        //     Ok(value) => println!("{:?}", value),
        //     Err(GpsError::NoData) => {}
        //     Err(GpsError::NoFix) => println!("gps: no fix"),
        //     Err(GpsError::ChecksumError) => println!("gps: checksum error"),
        // }

        // {
        // serde json to string fials if nan or inf introduced, ugh... may need to swap to json5
        // FIXME: This should convert the struct in json... ?
        // send compass_data out
        // db::Record::from_struct(Utc::now().timestamp(), &compass_data);

        // FIXME: move all jwon logic to the end here...
        //     match serde_json::to_string(&compass_data) {
        //         Ok(json_string) => println!("Compact: {}", json_string),
        //         Err(e) => eprintln!("Serialization failed: {}", e),
        //     }
        // } else
        //     // mark compass data as invalid for controller?
        // }
        // step()
        // write()

        // send_telem()

        thread::sleep(Duration::from_millis(100));
    }
}
